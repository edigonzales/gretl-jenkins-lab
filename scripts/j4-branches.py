#!/usr/bin/env python3
"""Verify the real developer job, branch pinning, and publication approval."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("j2_e2e", Path(__file__).with_name("j2-e2e.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--incompatible-branch")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    passwords = e2e.credentials(args.standalone)
    admin = e2e.Jenkins(args.port, "admin", passwords["admin"])
    operator = e2e.Jenkins(args.port, "operator", passwords["operator"])
    reviewer = e2e.Jenkins(args.port, "reviewer", passwords["reviewer"])
    jobs = Path(os.environ.get("JOBS_REPOSITORY", e2e.ROOT.parent / "gretl-runtime-jobs"))

    def revision(branch):
        return subprocess.check_output(["git", "-C", str(jobs), "ls-remote", "origin",
                                        "refs/heads/" + branch], text=True).split()[0]

    name = "gretl-dev-hello-gretl"
    branch_sha = revision(args.branch)
    endpoint = f"/job/{name}/buildWithParameters"
    dev = {"GRETL_DEV_BRANCH": args.branch, "GRETL_DEV_SHA": branch_sha, "J2_message": "J4 branch suite"}
    preview = f"{admin.base}/gretl-console/hello-gretl/branchPreview?" + urlencode({"branch": args.branch})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(admin.base + "/login")
        page.locator("[name=j_username]").fill("admin")
        page.locator("[name=j_password]").fill(passwords["admin"])
        page.locator("button[type=submit]").click()
        page.wait_for_url(admin.base + "/")
        page.goto(preview)
        button = page.get_by_role("button", name="Mit diesem Branch starten")
        if args.disabled:
            expect(button).to_have_count(0)
            admin.call(endpoint, dev, expected=(404, 405))
            print("Development action unavailable when disabled")
            return
        expect(button).to_be_visible()
        before = admin.json(f"/job/{name}/api/json")["nextBuildNumber"]
        page.get_by_label("Nachricht").fill("J4 branch from console")
        button.click()
        page.wait_for_url(f"{admin.base}/gretl-console/dev-hello-gretl/")
        build = f"/job/{name}/{before}/"
        e2e.poll(lambda: admin.call(build + "api/json", expected=(200, 404))[1].get("Content-Type", "").startswith("application/json"))
        log = e2e.finish(admin, build, "SUCCESS", args.evidence)
        assert branch_sha in log and "J4 branch marker hello" in log
        page.screenshot(path=args.evidence / "development-run.png")

    operator.call(endpoint, dev, expected=(403, 404))
    reviewer.call(endpoint, dev, expected=(403, 404))

    deployed = (e2e.ROOT / "jenkins/j2-jobs-ref").read_text().strip()
    queue = operator.start("hello-gretl", {"message": "Stable catalog execution"})
    normal = e2e.queued_build(admin, queue)
    normal_log = e2e.finish(admin, normal, "SUCCESS", args.evidence)
    assert f"@{deployed} runtime" in normal_log and "J4 branch marker" not in normal_log

    def start(target, form, result, upload=None):
        _, headers = admin.call(f"/job/gretl-dev-{target}/buildWithParameters", form, upload, expected=(201,))
        build = e2e.queued_build(admin, e2e.urlsplit(headers["Location"]).path)
        return build if result is None else e2e.finish(admin, build, result, args.evidence)

    assert "Invalid GRETL Git branch" in start("hello-gretl", dev | {"GRETL_DEV_BRANCH": "main~1"}, "FAILURE")
    start("hello-gretl", dev | {"GRETL_DEV_BRANCH": "j4-missing-fixture-20261114"}, "FAILURE")
    assert "The branch head changed" in start("hello-gretl", dev | {"GRETL_DEV_SHA": "0" * 40}, "FAILURE")
    if args.incompatible_branch:
        mismatch = dev | {"GRETL_DEV_BRANCH": args.incompatible_branch,
                          "GRETL_DEV_SHA": revision(args.incompatible_branch)}
        assert "The deployed Schema V2 contract changed" in start("hello-gretl", mismatch, "FAILURE")

    publication = (jobs / "testdata/publication.csv").read_bytes()
    flow = start("csv-publication", {k: dev[k] for k in ("GRETL_DEV_BRANCH", "GRETL_DEV_SHA")},
                 None, [("J2_inputFile", "publication.csv", publication)])
    e2e.poll(lambda: b"Proceed" in admin.call(flow + "input/Review/", expected=(200, 404))[0])
    review = {"json": json.dumps({"parameter": [{"name": "publicationNote", "value": "Development review"}]}),
              "proceed": "Proceed"}
    operator.call(flow + "input/Review/proceed", review, expected=(400, 403, 404))
    reviewer.call(flow + "input/Review/proceed", review, expected=(200, 302, 303))
    log = e2e.finish(admin, flow, "SUCCESS", args.evidence)
    assert "J4 branch marker prepare" in log
    assert re.findall(r"GRETL checkout-end \d+ ([a-f0-9]{40})", log) == [branch_sha, branch_sha]
    assert admin.call(flow + "artifact/publish/published.csv")[0] == publication


if __name__ == "__main__":
    main()
