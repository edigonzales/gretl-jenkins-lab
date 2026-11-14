#!/usr/bin/env python3
"""Inspect the timed-out GRETL process tree on the agent before workspace cleanup."""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.request import Request
from xml.etree.ElementTree import Element, SubElement, tostring
from uuid import uuid4


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = load("j2-e2e")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    admin = e2e.Jenkins(args.port, "admin", e2e.credentials(args.standalone)["admin"])
    revision = (e2e.ROOT / "jenkins/j2-jobs-ref").read_text().strip()
    model = {
        "id": "j4-timeout", "url": "https://github.com/edigonzales/gretl-runtime-jobs.git",
        "runtime": "gretl-j4-agent:local", "revision": revision,
        "agentLabel": os.environ.get("GRETL_AGENT_LABEL", "gretl-j4"),
        "parameters": {}, "submitters": "", "execution": {"timeoutSeconds": 180},
        "steps": [{"id": "wait", "job": "wait", "inputs": {}}],
        "jobs": {"wait": {"source": "testdata/jenkins/job.yaml", "script": "contract.groovy",
                          "parameters": {},
                          "outputs": {}, "execution": {"timeoutSeconds": 15}}},
    }
    generator = load("generate-jobs")
    script = (e2e.ROOT / "jenkins/gretl.Jenkinsfile").read_text()
    script = script.replace("'@MODEL@'", generator.literal(json.dumps(model)), 1)
    # Instrument only the synthetic job. All three samples use agent uptime.
    fixture = (e2e.ROOT / "fixtures/timeout-tree.groovy").read_text()
    runtime = '            try {\n                withEnv(["GRETL_SCRIPT='
    assert script.count(runtime) == 1
    script = script.replace(runtime,
                            "            writeFile(file: 'source/testdata/jenkins/contract.groovy', text: "
                            + generator.literal(fixture) + ")\n" + runtime)
    command = '                            exec timeout --kill-after=5s '
    assert script.count(command) == 1
    script = script.replace(command, '                            read -r started remainder < /proc/uptime\n'
                            '                            echo "$started" > work/timeout-start\n' + command)
    cleanup = "        } finally {\n            deleteDir()\n        }\n    }\n    return produced"
    assert script.count(cleanup) == 1
    inspection = (e2e.ROOT / "fixtures/timeout-tree-inspect.sh").read_text()
    if args.standalone:
        inspection = inspection.replace("set -eu\n", "set -eu\nWAIT_FOR_REAP=1\n", 1)
    script = script.replace(cleanup, cleanup.replace(
        "            deleteDir()",
        "            try {\n                sh " + generator.literal(inspection)
        + "\n            } finally {\n                deleteDir()\n            }"
    ))
    xml = Element("flow-definition")
    definition = SubElement(xml, "definition", {"class": "org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"})
    SubElement(definition, "script").text = script
    SubElement(definition, "sandbox").text = "true"
    crumb = admin.json("/crumbIssuer/api/json")
    name = "j4-e2e-timeout-" + uuid4().hex[:8]
    request = Request(admin.base + "/createItem?name=" + name, tostring(xml),
                      {"Content-Type": "application/xml", crumb["crumbRequestField"]: crumb["crumb"]})
    with admin.opener.open(request, timeout=60) as response:
        assert response.code == 200
    try:
        _, headers = admin.call(f"/job/{name}/build", {}, expected=(201,))
        build = e2e.queued_build(admin, e2e.urlsplit(headers["Location"]).path)
        log = e2e.finish(admin, build, "ABORTED", args.evidence)
        assert "ACCEPTANCE_PROCESS_STARTED" in log
        assert "ACCEPTANCE_PROCESS_FINISHED" not in log
        assert "GRETL exceeded its execution limit" in log
        topology = {}
        for role, pid, parent, group, started, state in re.findall(
                r"TIMEOUT_TREE (gretl|child|grandchild) (\d+) (\d+) (\d+) (\d+) (\w+)$", log, re.M):
            topology[role] = (int(pid), int(parent), int(group), int(started), state)
        assert set(topology) == {"gretl", "child", "grandchild"}, topology
        gretl, child, grandchild = (topology[role] for role in ("gretl", "child", "grandchild"))
        assert child[1] == gretl[0] and grandchild[1] == child[0], topology
        assert len({process[0] for process in topology.values()}) == 3
        assert len({process[2] for process in topology.values()}) == 1
        allowed_states = {"gone"} if args.standalone else {"gone", "Z"}
        assert all(process[4] in allowed_states for process in topology.values()), topology
        assert f"TIMEOUT_GROUP_STOPPED {gretl[2]}" in log and "TIMEOUT_GROUP_SURVIVOR" not in log
        assert "(exit 137)" in log
        assert '+ exec timeout --kill-after=5s 15 ' in log
        samples = []
        for marker in ("TIMEOUT_START", "GRETL JVM received TERM", "TIMEOUT_OBSERVED"):
            values = re.findall(rf"{marker} ([0-9]+\.[0-9]+)\r?$", log, re.M)
            assert len(values) == 1, f"Expected one {marker} marker, found {values}. See {args.evidence}"
            samples.append(float(values[0]))
        started_at, signaled_at, observed_at = samples
        term_elapsed = signaled_at - started_at
        elapsed = observed_at - started_at
        # The command allows 15s before TERM plus 5s before KILL. Allow for
        # uptime's centisecond resolution. The upper bound includes inspection.
        assert 14.98 <= term_elapsed < 19, term_elapsed
        assert 19.98 <= elapsed < 30, elapsed
        if not args.standalone:
            agent = re.search(r"Running on (\S+) in ", log).group(1)
            e2e.poll(lambda: f"pod/{agent}" not in e2e.kube("get", "pods", "-o", "name").splitlines(),
                     seconds=150)
            print(f"Agent pod {agent} removed.", flush=True)
        print(f"{build} TERM after {term_elapsed:.1f}s, escalated to KILL. "
              f"Inspection confirmed no live process in the group at {elapsed:.1f}s.",
              flush=True)
    finally:
        admin.call(f"/job/{name}/doDelete", {}, expected=(302, 303, 404))


if __name__ == "__main__":
    main()
