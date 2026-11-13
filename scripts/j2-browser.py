#!/usr/bin/env python3
"""Run the CSV publication through classic Jenkins using two browser sessions."""
import argparse
import importlib.util
from pathlib import Path

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("j2_e2e", Path(__file__).with_name("j2-e2e.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    password = e2e.credentials()
    base = f"http://127.0.0.1:{args.port}"
    fixture = e2e.ROOT.parent / "gretl-runtime-jobs/testdata/publication.csv"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        operator = browser.new_page()
        reviewer = browser.new_page(accept_downloads=True)
        for page, name in [(operator, "operator"), (reviewer, "reviewer")]:
            page.goto(base + "/login")
            page.locator('[name=j_username]').fill(name)
            page.locator('[name=j_password]').fill(password[name])
            page.locator('button[type=submit]').click()
            page.wait_for_url(base + "/")
        operator.goto(base + "/job/gretl-csv-publication/build?delay=0sec")
        operator.locator('input[type=file]').set_input_files(fixture)
        operator.screenshot(path=args.evidence / "1-upload.png")
        with operator.expect_response(lambda response: response.request.method == "POST" and
                                      "/job/gretl-csv-publication/build" in response.url) as submission:
            operator.get_by_role("button", name="Build", exact=True).click()
        response = submission.value
        location = response.headers.get("location", "")
        if "/queue/item/" in location:
            from urllib.parse import urlsplit
            auth = e2e.Jenkins(args.port, "operator", password["operator"])
            build = e2e.queued_build(auth, urlsplit(location).path)
        else:
            # Classic build form redirects to the job page. Find the submitted run
            # from the Build History link with the greatest visible build number.
            operator.goto(base + "/job/gretl-csv-publication/")
            link = operator.locator('a[href*="/job/gretl-csv-publication/"][href$="/console"]').first
            link.wait_for()
            build = link.get_attribute("href").rsplit("console", 1)[0]
        client = e2e.Jenkins(args.port, "reviewer", password["reviewer"])
        e2e.poll(lambda: b"Proceed" in client.call(build + "input/Review/", expected=(200, 404))[0])
        report = reviewer.request.get(base + build + "artifact/prepare/review.json")
        assert report.ok and report.json()["records"] == 3
        reviewer.goto(base + build + "input/")
        reviewer.locator('input[name=value]').fill("Freigabe aus der Jenkins-Oberfläche")
        reviewer.screenshot(path=args.evidence / "2-approval.png")
        reviewer.get_by_role("button", name="Proceed", exact=True).click()
        e2e.finish(client, build, "SUCCESS", args.evidence)
        reviewer.goto(base + build)
        with reviewer.expect_download() as download:
            reviewer.get_by_role("link", name="published.csv").click()
        download.value.save_as(args.evidence / "published.csv")
        assert (args.evidence / "published.csv").read_bytes() == fixture.read_bytes()
        browser.close()


if __name__ == "__main__":
    main()
