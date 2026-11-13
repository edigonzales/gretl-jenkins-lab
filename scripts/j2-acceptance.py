#!/usr/bin/env python3
"""Exercise failure paths and revision changes against the dedicated Jenkins lab."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory

import yaml


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = load("j2-e2e")
generator = load("generate-jobs")
ROOT = e2e.ROOT
CONFIG = "jenkins-jenkins-config-gretl"


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def snapshot():
    return json.loads(e2e.kube("get", "cm", CONFIG, "-o", "json"))


def apply(script):
    with NamedTemporaryFile(mode="w") as patch:
        json.dump({"data": {"gretl.yaml": script}}, patch)
        patch.flush()
        e2e.kube("patch", "cm", CONFIG, "--type=merge", "--patch-file", patch.name)


def report(evidence, name, value):
    (evidence / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def definition(name, mode, *, optional=None, uploads=False, successor=None):
    params = {}
    if uploads:
        params = {name: dict(name=name, label=name, type="file", required=True, accept=[".csv"])
                  for name in ("firstFile", "secondFile")}
    params["mode"] = dict(name="mode", label="Fixture mode", type="text", required=True, default=mode)
    outputs = {}
    if optional is not None:
        outputs["result"] = dict(name="result", label="Test output", path="result.txt", parameter="output",
                                 optional=optional)
    result = dict(id=name, name=name, description="Synthetic J2 acceptance fixture", parameters=params,
                  source="testdata/jenkins/job.yaml", script="contract.groovy", outputs=outputs,
                  execution={"serial": True, "timeoutSeconds": 8 if mode == "wait" else 90},
                  access={"run": {"users": ["operator"]}})
    if successor:
        result["onSuccess"] = {"job": successor}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--previous-ref", default="c4bbba0d2da981aabd4d9401d2267540fef9e666")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    users = {name: e2e.Jenkins(args.port, name, password) for name, password in e2e.credentials().items()}
    admin, operator, reviewer = (users[name] for name in ("admin", "operator", "reviewer"))
    repo = ROOT.parent / "gretl-runtime-jobs"
    rev = (ROOT / "jenkins/j2-jobs-ref").read_text().strip()
    previous = generator.git(repo, "rev-parse", args.previous_ref + "^{commit}")
    image = (ROOT / ".local/j2-image-ref").read_text().strip()
    url = generator.git(repo, "remote", "get-url", "origin")
    pipeline = (ROOT / "jenkins/gretl.Jenkinsfile").read_text()
    original = snapshot()["data"]["gretl.yaml"]
    records = {}

    def mark(ids, detail):
        for id in ids.split():
            records[id] = detail
        report(evidence, "checks", records)
        print(ids, detail, flush=True)

    def publish_config(contents, expected):
        apply(contents)
        e2e.poll(lambda: expected in admin.call("/job/gretl-csv-publication/config.xml")[0].decode())

    def run(name, expected, form=None, upload=None):
        build = e2e.queued_build(admin, operator.start(name, form, upload))
        return build, e2e.finish(admin, build, expected, evidence)

    def artifact(build, path):
        return admin.call(build + "artifact/" + path)[0]

    def script_text(script):
        body = admin.call("/scriptText", {"script": script})[0].decode()
        if "Exception:" in body or "startup failed:" in body:
            raise RuntimeError(body)
        return body.strip()

    names = {j["name"] for j in admin.json("/api/json?tree=jobs[name]")["jobs"]}
    require({"gretl-hello-gretl", "gretl-csv-publication"} <= names, "Public definitions missing")
    require(not {"gretl-publish-csv", "gretl-prepare-csv", "gretl-update-index"} & names, "Internal job exposed")
    mark("E01 E02 E03 E72", "Jenkins job inventory")

    with TemporaryDirectory() as checkout:
        subprocess.run(["git", "clone", "-q", str(repo), checkout], check=True)
        subprocess.run(["git", "-C", checkout, "remote", "set-url", "origin", url], check=True)
        cases = [
            ("E04", "jobs/csv-analyzer/job.yaml", "schemaVersion: 2", "schemaVersion: 1", "schemaVersion"),
            ("E05", "jobs/csv-analyzer/job.yaml", "id: csv-analyzer", "id: hello-gretl", "duplicate id"),
            ("E06", "flows/csv-publication/flow.yaml", "job: publish-csv", "job: missing-job", "unavailable step job"),
        ]
        version = snapshot()["metadata"]["resourceVersion"]
        for id, file, old, new, diagnostic in cases:
            subprocess.run(["git", "-C", checkout, "checkout", "-q", "--detach", rev], check=True)
            path = Path(checkout) / file
            path.write_text(path.read_text().replace(old, new, 1))
            subprocess.run(["git", "-C", checkout, "add", file], check=True)
            subprocess.run(["git", "-C", checkout, "-c", "user.name=J2", "-c", "user.email=j2@example.invalid",
                            "commit", "-qm", id], check=True)
            result = subprocess.run(["./scripts/deploy-jobs.sh"], text=True, capture_output=True,
                                    env={**os.environ, "JOBS_REPOSITORY": checkout, "JOBS_REF": "HEAD"})
            require(result.returncode and diagnostic in result.stderr, result.stderr)
            require(snapshot()["metadata"]["resourceVersion"] == version, "Invalid catalog changed active config")
            mark(id, result.stderr.strip())
        mark("E08", "All three rejected catalogs preserved ConfigMap " + version)

    publish_config(original, rev)
    subprocess.run(["./scripts/deploy-jobs.sh"], check=True)
    require(snapshot()["data"]["gretl.yaml"] == original, "Repeated deployment changed DSL")
    require({j["name"] for j in admin.json("/api/json?tree=jobs[name]")["jobs"]} == names, "Job inventory changed")
    mark("E07", "Identical DSL and Jenkins inventory after redeployment")

    message = "Wörtlich: Dollar $HOME, Semikolon ; und Anführungszeichen \""
    build, log = run("hello-gretl", "SUCCESS", {"message": message, "uppercase": "false"})
    require(message + " phase=script" in log, "Text was transformed")
    mark("E09 E10 E14 E21", build + " literal text retained through GRETL")

    sample = (repo / "testdata/sample.csv").read_bytes()
    build, log = run("csv-analyzer", "SUCCESS", {"delimiter": ";", "hasHeader": "false"},
                     ("inputFile", "semicolon.csv", b"id;name\n1;Bern\n"))
    require(json.loads(artifact(build, "csv-analyzer/report.json")) == {"records": 2, "columns": 2}, log)
    mark("E15 E31", build + " alternate upload produced its own two-row result")
    body, headers = operator.call("/job/gretl-csv-analyzer/buildWithParameters",
                                  {"J2_delimiter": "unsupported"}, expected=(400, 500))
    require(not headers.get("Location"), "Unsupported choice was queued")
    if b"Illegal choice" not in body:
        controller_log = e2e.kube("logs", "jenkins-0", "-c", "jenkins", "--since=30s")
        require("Illegal choice for parameter J2_delimiter: unsupported" in controller_log, "Missing native choice rejection")
    mark("E16", "Jenkins rejected unsupported choice before queueing it")
    build = e2e.queued_build(admin, operator.start("csv-analyzer",
                              upload=("inputFile", "../suspicious.csv", sample)))
    outcome = e2e.poll(lambda: admin.json(build + "api/json").get("result"))
    require(outcome in {"SUCCESS", "FAILURE"}, str(outcome))
    log = e2e.finish(admin, build, outcome, evidence)
    if outcome == "SUCCESS":
        require("inputFile/J2_inputFile inputFile.csv" in log and "../suspicious.csv" not in log, log[-2000:])
        require(json.loads(artifact(build, "csv-analyzer/report.json")) == {"records": 3, "columns": 2}, log)
    else:
        require("Invalid filename" in log or "Invalid upload extension" in log, log[-2000:])
    mark("E27", build + " path-like upload handled without using it as a filesystem path")

    # Jenkins' native buildWithParameters ignores undeclared form keys. Submit a
    # ParametersAction through the local controller to exercise the pipeline guard.
    queue_id = script_text("""
def job = jenkins.model.Jenkins.get().getItem('gretl-hello-gretl')
def future = jenkins.model.Jenkins.get().queue.schedule2(job, 0,
  new hudson.model.ParametersAction(new hudson.model.StringParameterValue('J2_undeclared', 'ignored')))
println(future.item.id)
""")
    build = e2e.queued_build(admin, f"/queue/item/{int(queue_id)}/")
    log = e2e.finish(admin, build, "FAILURE", evidence)
    require("Unexpected parameters:" in log, log[-1000:])
    mark("E20", build + " rejected an undeclared parameter in the Jenkins build action")

    fixtures = [
        definition("acceptance-required", "ok", optional=False),
        definition("acceptance-optional", "ok", optional=True),
        definition("acceptance-files", "files", optional=False, uploads=True),
        definition("acceptance-first-fails", "fail", successor="acceptance-successor"),
        definition("acceptance-successor-fails", "ok", successor="acceptance-successor"),
        definition("acceptance-successor", "fail"),
    ]
    catalog = {d["id"]: d for d in fixtures}
    script = yaml.safe_load(original)
    dsl = script["jobs"][0]["script"]
    try:
        additions = [generator.render(d, catalog, url, rev, image, pipeline) for d in fixtures[:-1]]
        # The outer timeout for this flow exceeds the job-level timeout.
        wait_job = definition("acceptance-wait", "wait")
        catalog[wait_job["id"]] = wait_job
        wait_flow = dict(wait_job, id="acceptance-timeout", execution={"serial": True, "timeoutSeconds": 90},
                         steps=[{"id": "wait", "job": wait_job["id"], "inputs": {"mode": {"value": "wait"}}}])
        additions.append(generator.render(wait_flow, catalog, url, rev, image, pipeline))
        invalid = definition("acceptance-revision", "ok")
        additions.append(generator.render(invalid, {invalid["id"]: invalid}, url, "0" * 40, image, pipeline))
        script["jobs"][0]["script"] = dsl + "\n" + "\n".join(additions)
        apply(yaml.safe_dump(script))
        e2e.poll(lambda: "gretl-acceptance-timeout" in {j["name"] for j in admin.json("/api/json?tree=jobs[name]")["jobs"]})
        build, log = run("acceptance-required", "FAILURE")
        require("Missing required output result" in log, log[-1500:])
        mark("E35", build + " missing required output failed")
        build, _ = run("acceptance-optional", "SUCCESS")
        admin.call(build + "artifact/acceptance-optional/result.txt", expected=(404,))
        mark("E36", build + " missing optional output passed")
        build, _ = run("acceptance-files", "SUCCESS",
                       upload=[("firstFile", "first.csv", b"id\n1\n"), ("secondFile", "second.csv", b"id\n2\n")])
        require(artifact(build, "acceptance-files/result.txt") == b"id\n1\nid\n2\n", "Wrong upload bytes")
        mark("E28", build + " two multipart uploads materialized separately")
        build, log = run("acceptance-first-fails", "FAILURE")
        require("(acceptance-successor)" not in log, "Successor ran after failure")
        failure = json.loads(artifact(build, "reports/acceptance-first-fails.json"))
        require(failure["execution"]["exitCode"] != 0, "Failure report has no nonzero exit code")
        mark("E53 E66 E67", build + " first job failed with archived runtime report and skipped successor")
        build, log = run("acceptance-successor-fails", "FAILURE")
        require("(acceptance-successor)" in log and "ACCEPTANCE_SUCCESS" in log, "Successor did not run")
        mark("E54", build + " successor failure failed the build")
        build, log = run("acceptance-revision", "FAILURE")
        require("Couldn't find any revision to build" in log and "GRETL runtime-start" not in log, log[-2500:])
        mark("E62", build + " invalid SHA failed before GRETL")
        build, log = run("acceptance-timeout", "ABORTED")
        require("ACCEPTANCE_PROCESS_STARTED" in log and "ACCEPTANCE_PROCESS_FINISHED" not in log
                and "Cancelling nested steps due to timeout" in log, log[-2000:])
        pod = re.search(r"Running on (gretl-j2-\S+)", log).group(1)
        workspace = "/home/jenkins/agent/workspace/gretl-acceptance-timeout"
        def no_process():
            output = e2e.kube("exec", pod, "-c", "gretl", "--", "sh", "-c",
                              "for f in /proc/[0-9]*/cmdline; do tr '\\000' ' ' < \"$f\" 2>/dev/null; echo; done")
            return not any(workspace in line and "java " in line for line in output.splitlines())
        e2e.poll(no_process, seconds=30)
        mark("E68", build + " GRETL started, Jenkins cancelled it, agent process exited")
    finally:
        apply(original)
        for name in [*(d["id"] for d in fixtures[:-1]), "acceptance-timeout", "acceptance-revision"]:
            admin.call("/job/gretl-" + name + "/doDelete", {}, expected=(302, 303, 404))

    def deploy(revision):
        subprocess.run(["./scripts/deploy-jobs.sh"], check=True, env={**os.environ, "JOBS_REF": revision})
        e2e.poll(lambda: revision in admin.call("/job/gretl-csv-publication/config.xml")[0].decode())

    publication = (repo / "testdata/publication.csv").read_bytes()
    flow = None
    try:
        deploy(previous)
        flow = e2e.queued_build(admin, operator.start("csv-publication",
                                upload=("inputFile", "revision-old.csv", publication)))
        e2e.poll(lambda: b"Proceed" in reviewer.call(flow + "input/Review/", expected=(200, 404))[0])
        deploy(rev)
        second = operator.start("csv-publication", upload=("inputFile", "revision-new.csv", publication + b"3000,Test\n"))
        blocked = e2e.poll(lambda: (lambda d: d if d.get("blocked") else None)(admin.json(second + "api/json")))
        report(evidence, "serial-queue", {"queue": second, "reason": blocked["why"]})
        mark("E55 E56 E58", "Second serial flow blocked while first waited for approval")
        form = {"json": json.dumps({"parameter": [{"name": "publicationNote", "value": "Revisionswechsel"}]}),
                "proceed": "Proceed"}
        reviewer.call(flow + "input/Review/proceed", form, expected=(200, 302, 303))
        log = e2e.finish(admin, flow, "SUCCESS", evidence)
        require(re.findall(r"GRETL checkout-end \d+ ([a-f0-9]{40})", log) == [previous, previous], log[-2000:])
        require(artifact(flow, "publish/published.csv") == publication, "Publication differs")
        mark("E45 E59 E60 E61", flow + " kept both checkouts on " + previous + " after redeploying " + rev)
        flow = e2e.queued_build(admin, second)
        e2e.poll(lambda: b"Proceed" in reviewer.call(flow + "input/Review/", expected=(200, 404))[0])
        reviewer.call(flow + "input/Review/abort", {}, expected=(200, 302, 303))
        log = e2e.finish(admin, flow, "ABORTED", evidence)
        require(rev in log, "Second build did not pick up the new pin")
        flow = None
    finally:
        if flow and admin.json(flow + "api/json")["building"]:
            admin.call(flow + "stop", {}, expected=(200, 302, 303))
        publish_config(original, rev)

    job = "/job/gretl-scheduled-report/"
    previous_build = (admin.json(job + "api/json?tree=lastBuild[number]")["lastBuild"] or {}).get("number", 0)
    cron = script_text("""
def job = jenkins.model.Jenkins.get().getItem('gretl-scheduled-report')
def timer = job.triggers.values().find { it.class.name == 'hudson.triggers.TimerTrigger' }
assert timer.spec == 'TZ=Europe/Zurich\\nH H(1-3) * * *'
timer.run()
println(timer.spec)
""")
    def timer_build():
        for build in admin.json(job + "api/json?tree=builds[number,actions[causes[shortDescription]]]")["builds"]:
            if build["number"] > previous_build and any(c.get("shortDescription") == "Started by timer"
                for a in build["actions"] for c in a.get("causes", [])):
                return job + str(build["number"]) + "/"
    build = e2e.poll(timer_build)
    e2e.finish(admin, build, "SUCCESS", evidence)
    require(artifact(build, "update-index/index.txt"), "Timer successor output missing")
    mark("E57", build + " timer used the same successor as manual builds, " + repr(cron))


if __name__ == "__main__":
    main()
