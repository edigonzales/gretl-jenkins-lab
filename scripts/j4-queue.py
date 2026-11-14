#!/usr/bin/env python3
"""Move a real Git branch while a developer build is blocked in Jenkins."""
import argparse
import hashlib
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

    publication = subprocess.check_output(
        ["git", "-C", str(args.jobs), "show", f"{old}:testdata/publication.csv"]
    )

    def enqueue(filename):
        _, headers = admin.call(job + "buildWithParameters", {"GRETL_DEV_BRANCH": branch, "GRETL_DEV_SHA": old},
                                [("J2_inputFile", filename, publication)], expected=(201,))
        return e2e.urlsplit(headers["Location"]).path

    def blocked_by(queue, build):
        item = admin.json(queue + "api/json")
        assert not item.get("cancelled") and not item.get("executable"), item
        reason = item.get("why") or ""
        return item if item["blocked"] and f"Build #{build.strip('/').split('/')[-1]}" in reason else None

    def checkouts(log):
        return re.findall(r"GRETL checkout-end (\d+) ([a-f0-9]{40})", log)

    def is_waiting(queue):
        try:
            item = admin.json(queue + "api/json")
        except e2e.JenkinsError as error:
            if error.status == 404:
                return False
            raise
        return not (item.get("cancelled") or item.get("executable"))

    first = second = blocker = rejected = None
    assert head() is None
    try:
        git("push", "--force-with-lease=" + ref + ":", REMOTE, old + ":" + ref)
        assert head() == old
        first = enqueue("queue-blocker.csv")
        blocker = e2e.queued_build(admin, first)
        e2e.poll(lambda: b"Proceed" in reviewer.call(blocker + "input/Review/", expected=(200, 404))[0])
        assert f"source {REMOTE}@{old} runtime " in admin.call(blocker + "consoleText")[0].decode()
        second = enqueue("queued-branch.csv")
        before = e2e.poll(lambda: blocked_by(second, blocker))
        git("push", f"--force-with-lease={ref}:{old}", REMOTE, new + ":" + ref)
        assert head() == new
        after = blocked_by(second, blocker)
        assert after, "The build must still be blocked after GitHub reports the new head"
        assert b"Proceed" in reviewer.call(blocker + "input/Review/")[0]
        before_approval = checkouts(admin.call(blocker + "consoleText")[0].decode())
        assert [sha for _, sha in before_approval] == [old], before_approval
        record = {"remote": REMOTE, "branch": branch, "oldHead": old, "newHead": new, "blocker": blocker,
                  "queueItem": second, "blockedBeforePush": before, "blockedAfterPush": after,
                  "headAtSnapshot": new, "checkoutsBeforeApproval": before_approval}
        (args.evidence / "queue-transition.json").write_text(json.dumps(record, indent=2) + "\n")

        review = {"json": json.dumps({"parameter": [{"name": "publicationNote", "value": "J4 queue test"}]}),
                  "proceed": "Proceed"}
        reviewer.call(blocker + "input/Review/proceed", review, expected=(200, 302, 303))
        log = e2e.finish(admin, blocker, "SUCCESS", args.evidence)
        completed = checkouts(log)
        assert completed[:1] == before_approval and len(completed) == 2 and completed[1][1] == old, completed
        published = admin.call(blocker + "artifact/publish/published.csv")[0]
        assert published == publication
        record["checkoutsAfterApproval"] = completed
        record["publishedSha256"] = hashlib.sha256(published).hexdigest()

        rejected = e2e.queued_build(admin, second)
        log = e2e.finish(admin, rejected, "FAILURE", args.evidence)
        params = {param["name"]: param["value"] for action in admin.json(
            rejected + "api/json?tree=actions[parameters[name,value]]"
        )["actions"] for param in action.get("parameters", [])
                  if param["name"] in {"GRETL_DEV_BRANCH", "GRETL_DEV_SHA"}}
        assert params["GRETL_DEV_BRANCH"] == branch and params["GRETL_DEV_SHA"] == old
        assert "The branch head changed. Select the branch again." in log
        assert "GRETL runtime-start" not in log and "GRETL checkout-start" not in log
        record["rejectedBuild"] = rejected
        record["rejectedParameters"] = params
        (args.evidence / "queue-transition.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"{branch} moved {old} -> {new} while {second} was blocked. {rejected} rejected before GRETL.")
    finally:
        try:
            queue_ids = set()
            for queue in (second, first):
                if queue is None:
                    continue
                queue_id = int(queue.strip("/").split("/")[-1])
                queue_ids.add(queue_id)
                if is_waiting(queue):
                    try:
                        admin.call(f"/queue/cancelItem?id={queue_id}", {}, expected=(200, 204, 302, 303))
                    except e2e.JenkinsError as error:
                        if error.status not in (404, 500) or is_waiting(queue):
                            raise
            # Started builds outlive their queue entries. Include already known paths
            # and resolve any handoff that took place before cancellation.
            builds = {build for build in (blocker, rejected) if build}
            if queue_ids:
                builds.update(e2e.urlsplit(run["url"]).path for run in admin.json(
                    job + "api/json?tree=builds[queueId,url]"
                )["builds"] if run["queueId"] in queue_ids)
            for build in builds:
                if admin.json(build + "api/json")["building"]:
                    admin.call(build + "stop", {}, expected=(200, 302, 303))
                    e2e.poll(lambda: not admin.json(build + "api/json")["building"], seconds=60)
        finally:
            current = head()
            if current is not None:
                assert current in {old, new}, f"Temporary ref {ref} unexpectedly points to {current}"
                git("push", f"--force-with-lease={ref}:{current}", REMOTE, ":" + ref)
                assert head() is None


if __name__ == "__main__":
    main()
