#!/usr/bin/env python3
"""Run the existing sleeping GRETL fixture and check Jenkins cancels it."""
import argparse
import importlib.util
import os
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
        "steps": [{"id": "wait", "job": "wait", "inputs": {"mode": {"value": "wait"}}}],
        "jobs": {"wait": {"source": "testdata/jenkins/job.yaml", "script": "contract.groovy",
                          "parameters": {"mode": {"name": "mode", "type": "text", "required": True}},
                          "outputs": {}, "execution": {"timeoutSeconds": 12}}},
    }
    generator = load("generate-jobs")
    import json
    script = (e2e.ROOT / "jenkins/gretl.Jenkinsfile").read_text()
    script = script.replace("'@MODEL@'", generator.literal(json.dumps(model)), 1)
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
        assert "Cancelling nested steps due to timeout" in log
    finally:
        admin.call(f"/job/{name}/doDelete", {}, expected=(302, 303, 404))


if __name__ == "__main__":
    main()
