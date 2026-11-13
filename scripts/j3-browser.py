#!/usr/bin/env python3
"""Verify the Console against the installed plugin and existing k3d jobs."""
import argparse
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("j2_e2e", Path(__file__).with_name("j2-e2e.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    passwords = e2e.credentials()
    base = f"http://127.0.0.1:{args.port}"
    console = base + "/gretl-console/"
    data = e2e.ROOT.parent / "gretl-runtime-jobs/testdata"
    admin = e2e.Jenkins(args.port, "admin", passwords["admin"])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        def login(user):
            page = browser.new_page(accept_downloads=True, viewport={"width": 1280, "height": 900})
            page.goto(base + "/login")
            page.locator("[name=j_username]").fill(user)
            page.locator("[name=j_password]").fill(passwords[user])
            page.locator("button[type=submit]").click()
            page.wait_for_url(base + "/")
            return page

        operator = login("operator")
        viewer = login("viewer")
        reviewer = login("reviewer")
        outsider = login("outsider")
        operator.goto(console)
        expect(operator.get_by_role("heading", name="GRETL Console")).to_be_visible()
        expect(operator.get_by_role("link", name="Hello GRETL")).to_be_visible()
        expect(operator.get_by_role("link", name="CSV prüfen und veröffentlichen")).to_be_visible()
        for internal in ("prepare-csv", "publish-csv", "update-index"):
            assert operator.request.get(console + internal + "/").status == 404
        operator.screenshot(path=evidence / "catalog.png")
        viewer.goto(console + "hello-gretl/")
        expect(viewer.get_by_role("heading", name="Hello GRETL")).to_be_visible()
        expect(viewer.get_by_role("button", name="Starten")).to_have_count(0)
        outsider.goto(console)
        expect(outsider.get_by_text("Keine GRETL-Jobs verfügbar.")).to_be_visible()
        assert outsider.request.get(console + "hello-gretl/").status == 404
        assert browser.new_page().request.get(console).status == 403

        operator.goto(console + "scheduled-report/")
        expect(operator.get_by_text("H H(1-3) * * * · Europe/Zurich", exact=True)).to_be_visible()
        expect(operator.get_by_text("Seriell", exact=True)).to_be_visible()

        def start(job):
            route = f"/job/gretl-{job}/"
            previous = (admin.json(route + "api/json?tree=lastBuild[number]")["lastBuild"] or {}).get("number", 0)
            with operator.expect_response(lambda r: r.request.method == "POST" and
                                          urlsplit(r.url).path == route + "build") as posted:
                operator.get_by_role("button", name="Starten").click()
            response = posted.value
            assert response.status == 303, f"{response.status}: {response.url}"
            location = urlsplit(response.headers.get("location", "")).path
            if location.startswith("/queue/item/"):
                return e2e.queued_build(admin, location)

            def new_build():
                last = admin.json(route + "api/json?tree=lastBuild[number]")["lastBuild"]
                return last["number"] if last and last["number"] > previous else None

            return f"{route}{e2e.poll(new_build)}/"

        operator.goto(console + "hello-gretl/")
        operator.get_by_label("Nachricht", exact=True).fill("Grüezi J3")
        operator.get_by_label("Wiederholungen", exact=True).fill("2")
        operator.get_by_label("Grossbuchstaben", exact=True).check()
        build = start("hello-gretl")
        assert e2e.finish(admin, build, "SUCCESS", evidence).count("GRÜEZI J3 phase=script") == 2

        operator.goto(console + "csv-quality-check/")
        expect(operator.get_by_label("Stichtag")).to_have_attribute("type", "date")
        operator.get_by_label("Stichtag").fill("2024-02-29")
        operator.get_by_label("Bemerkungen").fill("Erste Zeile\nZweite Zeile")
        operator.get_by_label("CSV-Datei").set_input_files(data / "municipalities.csv")
        operator.screenshot(path=evidence / "form.png", full_page=True)
        build = start("csv-quality-check")
        e2e.finish(admin, build, "SUCCESS", evidence)
        result = admin.json(build + "artifact/csv-quality-check/quality-report.json")
        assert result["notes"] == "Erste Zeile\nZweite Zeile" and result["referenceDate"] == "2024-02-29"

        operator.goto(console + "csv-analyzer/")
        operator.get_by_label("Trennzeichen").select_option(label="Semikolon")
        operator.get_by_label("Erste Zeile enthält Spaltennamen").uncheck()
        operator.get_by_label("CSV-Datei").set_input_files(
            {"name": "semicolon.csv", "mimeType": "text/csv", "buffer": b"id;name\n1;Bern\n"})
        build = start("csv-analyzer")
        e2e.finish(admin, build, "SUCCESS", evidence)
        assert admin.json(build + "artifact/csv-analyzer/report.json") == {"records": 2, "columns": 2}

        operator.goto(console + "csv-publication/")
        operator.get_by_label("CSV-Datei").set_input_files(data / "publication.csv")
        flow = start("csv-publication")
        reviewer.goto(console + "csv-publication/")
        client = e2e.Jenkins(args.port, "reviewer", passwords["reviewer"])
        e2e.poll(lambda: b"Proceed" in client.call(flow + "input/Review/", expected=(200, 404))[0])
        denied, _ = e2e.Jenkins(args.port, "operator", passwords["operator"]).call(
            flow + "input/Review/proceed", {}, expected=(400, 403))
        assert b"You need to be" in denied
        reviewer.reload()
        row = reviewer.get_by_role("row").filter(has=reviewer.locator(f'a[href="{flow}"]'))
        expect(row.get_by_role("link", name="Wartet auf Freigabe")).to_be_visible()
        expect(row.get_by_role("link", name="Artefakte", exact=False)).to_be_visible()
        reviewer.screenshot(path=evidence / "approval.png")
        row.get_by_role("link", name="Wartet auf Freigabe").click()
        reviewer.locator('div[name=parameter]:has(input[value=publicationNote]) input[name=value]').fill("J3 freigegeben")
        reviewer.get_by_role("button", name="Proceed", exact=True).click()
        e2e.finish(admin, flow, "SUCCESS", evidence)
        assert admin.json(flow + "artifact/publish/publication.json")["note"] == "J3 freigegeben"
        reviewer.goto(console + "csv-publication/")
        row = reviewer.get_by_role("row").filter(has=reviewer.locator(f'a[href="{flow}"]'))
        row.get_by_role("link", name="Artefakte", exact=False).click()
        reviewer.get_by_role("link", name="publish").click()
        with reviewer.expect_download() as download:
            reviewer.get_by_role("link", name="published.csv").click()
        downloaded = evidence / "published.csv"
        download.value.save_as(downloaded)
        assert downloaded.read_bytes() == (data / "publication.csv").read_bytes()
        browser.close()


if __name__ == "__main__":
    main()
