#!/usr/bin/env python3
"""Move a real Git branch while a developer build is blocked in Jenkins."""
import argparse
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

spec = importlib.util.spec_from_file_location("j2_e2e", Path(__file__).with_name("j2-e2e.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)
REMOTE = "https://github.com/edigonzales/gretl-runtime-jobs.git"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--new-head", required=True, help="Local descendant of the deployed catalog commit")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    passwords = e2e.credentials(args.standalone)
    admin = e2e.Jenkins(args.port, "admin", passwords["admin"])
    reviewer = e2e.Jenkins(args.port, "reviewer", passwords["reviewer"])
    job = "/job/gretl-dev-csv-publication/"
    old = (e2e.ROOT / "jenkins/j2-jobs-ref").read_text().strip()

    def git(*argv):
        return subprocess.check_output(["git", "-C", str(args.jobs), *argv], text=True).strip()

    new = git("rev-parse", "--verify", "--end-of-options", args.new_head + "^{commit}")
    assert old != new
    git("merge-base", "--is-ancestor", old, new)
    branch = "test/j4-queue-" + uuid4().hex[:16]
    ref = "refs/heads/" + branch

    def head():
        result = git("ls-remote", REMOTE, ref)
        return result.split()[0] if result else None

    publication = (args.jobs / "testdata/publication.csv").read_bytes()

    def enqueue(filename):
        _, headers = admin.call(job + "buildWithParameters", {"GRETL_DEV_BRANCH": branch, "GRETL_DEV_SHA": old},
                                [("J2_inputFile", filename, publication)], expected=(201,))
        return e2e.urlsplit(headers["Location"]).path

    def blocked_by(queue, build):
        item = admin.json(queue + "api/json")
        assert not item.get("cancelled") and not item.get("executable"), item
        reason = item.get("why") or ""
        return reason if item["blocked"] and f"Build #{build.strip('/').split('/')[-1]}" in reason else None

    current = old
    first = second = None
    git("push", "--force-with-lease=" + ref + ":", REMOTE, old + ":" + ref)
    try:
        assert head() == old
        first = enqueue("queue-blocker.csv")
        blocker = e2e.queued_build(admin, first)
        e2e.poll(lambda: b"Proceed" in reviewer.call(blocker + "input/Review/", expected=(200, 404))[0])
        second = enqueue("queued-branch.csv")
        before = e2e.poll(lambda: blocked_by(second, blocker))
        git("push", f"--force-with-lease={ref}:{old}", REMOTE, new + ":" + ref)
        current = new
        assert head() == new
        after = blocked_by(second, blocker)
        assert after, "The build must still be blocked after GitHub reports the new head"
        record = {"branch": branch, "oldHead": old, "newHead": new, "blocker": blocker,
                  "queueItem": second, "blockedBeforePush": before, "blockedAfterPush": after}
        (args.evidence / "queue-transition.json").write_text(json.dumps(record, indent=2) + "\n")

        review = {"json": json.dumps({"parameter": [{"name": "publicationNote", "value": "J4 queue test"}]}),
                  "proceed": "Proceed"}
        reviewer.call(blocker + "input/Review/proceed", review, expected=(200, 302, 303))
        log = e2e.finish(admin, blocker, "SUCCESS", args.evidence)
        assert re.findall(r"GRETL checkout-end \d+ ([a-f0-9]{40})", log) == [old, old]
        assert admin.call(blocker + "artifact/publish/published.csv")[0] == publication

        rejected = e2e.queued_build(admin, second)
        log = e2e.finish(admin, rejected, "FAILURE", args.evidence)
        params = {param["name"]: param["value"] for action in admin.json(
            rejected + "api/json?tree=actions[parameters[name,value]]"
        )["actions"] for param in action.get("parameters", [])
                  if param["name"] in {"GRETL_DEV_BRANCH", "GRETL_DEV_SHA"}}
        assert params["GRETL_DEV_BRANCH"] == branch and params["GRETL_DEV_SHA"] == old
        assert "The branch head changed. Select the branch again." in log
        assert "GRETL runtime-start" not in log and "GRETL checkout-end" not in log
        record["rejectedBuild"] = rejected
        record["rejectedParameters"] = params
        (args.evidence / "queue-transition.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"{branch} moved {old} -> {new} while {second} was blocked. {rejected} rejected before GRETL.")
    finally:
        try:
            for queue in (second, first):
                if queue is None:
                    continue
                body, headers = admin.call(queue + "api/json", expected=(200, 404))
                if not headers.get("Content-Type", "").startswith("application/json"):
                    continue
                item = json.loads(body)
                executable = item.get("executable")
                if executable:
                    build = e2e.urlsplit(executable["url"]).path
                    if admin.json(build + "api/json")["building"]:
                        admin.call(build + "stop", {}, expected=(200, 302, 303))
                elif not item.get("cancelled"):
                    admin.call("/queue/cancelItem?id=" + queue.strip("/").split("/")[-1], {},
                               expected=(200, 204, 302, 303, 404))
        finally:
            git("push", f"--force-with-lease={ref}:{current}", REMOTE, ":" + ref)


if __name__ == "__main__":
    main()
