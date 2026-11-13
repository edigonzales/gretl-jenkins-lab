#!/usr/bin/env python3
"""Exercise the real Jenkins lab over HTTP, including multipart upload and restart."""
import argparse
import base64
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request
from uuid import uuid4

from bench import ROOT, kube


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Jenkins:
    def __init__(self, port, user, password):
        self.base = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.opener.addheaders = [("Authorization", "Basic " + auth)]

    def call(self, path, form=None, upload=None, expected=(200,)):
        headers = {}
        data = None
        if form is not None:
            crumb = self.json("/crumbIssuer/api/json")
            headers[crumb["crumbRequestField"]] = crumb["crumb"]
            data = urlencode(form).encode()
            if upload:
                name, filename, content = upload
                boundary = uuid4().hex
                body = bytearray()
                for field, value in form.items():
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                            'Content-Type: application/octet-stream\r\n\r\n'.encode())
                body.extend(content)
                body.extend(f"\r\n--{boundary}--\r\n".encode())
                headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
                data = body
        try:
            response = self.opener.open(Request(self.base + path, data, headers), timeout=90)
        except HTTPError as exc:
            response = exc
        with response:
            if response.code not in expected:
                raise RuntimeError(f"{path}: HTTP {response.code}, expected {expected}")
            return response.read(), response.headers

    def json(self, path):
        return json.loads(self.call(path)[0])

    def start(self, name, form=None, upload=None, parameterized=True):
        endpoint = "buildWithParameters" if parameterized else "build"
        fields = {"J2_" + key: value for key, value in (form or {}).items()}
        if upload:
            upload = ("J2_" + upload[0], *upload[1:])
        _, headers = self.call(f"/job/gretl-{name}/{endpoint}", fields, upload, expected=(201,))
        return urlsplit(headers["Location"]).path


def poll(check, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (URLError, ConnectionError):
            pass
        time.sleep(1)
    raise TimeoutError("Jenkins condition timed out")


def queued_build(client, queue):
    return poll(lambda: urlsplit(client.json(queue + "api/json").get("executable", {}).get("url", "")).path)


def credentials():
    admin = json.loads(kube("get", "secret", "jenkins", "-o", "json"))["data"]
    local = json.loads(kube("get", "secret", "jenkins-j2-users", "-o", "json"))["data"]
    decode = lambda value: base64.b64decode(value).decode()
    return {decode(admin["jenkins-admin-user"]): decode(admin["jenkins-admin-password"]),
            **{user: decode(password) for user, password in local.items()}}


def finish(client, build, expected, evidence):
    result = poll(lambda: client.json(build + "api/json").get("result"))
    log = client.call(build + "consoleText")[0].decode()
    (evidence / (build.strip("/").replace("/", "-") + ".log")).write_text(log)
    assert result == expected, f"{build}: {result}, expected {expected}. See {evidence}"
    print(f"{build} {result}", flush=True)
    return log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--large", action="store_true")
    parser.add_argument("--flows-only", action="store_true")
    parser.add_argument("--examples", action="store_true")
    parser.add_argument("--skip-flows", action="store_true")
    args = parser.parse_args()
    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    users = {name: Jenkins(args.port, name, password) for name, password in credentials().items()}
    admin, operator, reviewer, viewer, outsider, dual = (users[u] for u in
        ("admin", "operator", "reviewer", "viewer", "outsider", "dual-role"))
    data = ROOT.parent / "gretl-runtime-jobs/testdata"
    sample = (data / "sample.csv").read_bytes()
    publication = (data / "publication.csv").read_bytes()

    assert {"bench", "gretl-hello-gretl", "gretl-csv-analyzer", "gretl-scheduled-report",
            "gretl-csv-publication"} <= {j["name"] for j in admin.json("/api/json?tree=jobs[name]")["jobs"]}
    viewer.call("/job/gretl-hello-gretl/buildWithParameters", {}, expected=(403,))
    outsider.call("/job/gretl-hello-gretl/api/json", expected=(404,))
    operator.call("/job/gretl-publish-csv/api/json", expected=(404,))

    def run(client, name, form, expected, upload=None):
        build = queued_build(admin, client.start(name, form, upload))
        return build, finish(admin, build, expected, evidence)

    if not args.flows_only:
        _, hello = run(operator, "hello-gretl", {"message": "Grüezi & Zürich", "count": "2", "uppercase": "true"}, "SUCCESS")
        assert hello.count("GRÜEZI & ZÜRICH phase=script") == 2
        assert 'openjdk version "25.' in hello
        _, invalid = run(operator, "hello-gretl", {"message": "invalid", "count": "2.5"}, "FAILURE")
        assert "Invalid integer parameter count" in invalid
        _, default = run(operator, "hello-gretl", {"message": "default count"}, "SUCCESS")
        assert default.count("default count phase=script") == 1
        started = time.monotonic()
        build, _ = run(operator, "csv-analyzer", {}, "SUCCESS", ("inputFile", "test upload.csv", sample))
        assert operator.json(build + "artifact/csv-analyzer/report.json") == {"records": 3, "columns": 2}
        operator.json(build + "artifact/reports/csv-analyzer.json")
        print(f"CSV submit through result {time.monotonic() - started:.2f}s", flush=True)
        _, missing = run(operator, "csv-analyzer", {}, "FAILURE")
        assert "Missing upload inputFile" in missing
        _, bad_extension = run(operator, "csv-analyzer", {}, "FAILURE", ("inputFile", "input.txt", sample))
        assert "Invalid upload extension" in bad_extension
        build, _ = run(operator, "scheduled-report", {}, "SUCCESS")
        assert operator.call(build + "artifact/update-index/index.txt")[0]

    def start_flow(client):
        build = queued_build(admin, client.start("csv-publication", upload=("inputFile", "publication.csv", publication)))
        poll(lambda: b"Proceed" in admin.call(build + "input/Review/", expected=(200, 404))[0])
        assert reviewer.json(build + "artifact/prepare/review.json")["records"] == 3
        return build

    if not args.skip_flows:
        flow = start_flow(operator)
        computers = admin.json("/computer/api/json?tree=computer[displayName,executors[currentExecutable[url]]]")
        assert not any((e.get("currentExecutable") or {}).get("url", "").endswith(flow)
                       for c in computers["computer"] for e in c["executors"])
        form = {"json": json.dumps({"parameter": [{"name": "publicationNote", "value": "Freigabe Zürich"}]}),
                "proceed": "Proceed"}
        denied, _ = operator.call(flow + "input/Review/proceed", form, expected=(400, 403))
        assert b"You need to be" in denied
        if args.restart:
            kube("rollout", "restart", "statefulset/jenkins")
            kube("rollout", "status", "statefulset/jenkins", "--timeout=150s", timeout=160)
            poll(lambda: b"Proceed" in admin.call(flow + "input/Review/", expected=(200, 404))[0])
        reviewer.call(flow + "input/Review/proceed", form, expected=(200, 302, 303))
        flow_log = finish(admin, flow, "SUCCESS", evidence)
        revisions = re.findall(r"GRETL checkout-end \d+ ([a-f0-9]{40})", flow_log)
        assert len(revisions) == 2 and revisions[0] == revisions[1]
        assert reviewer.call(flow + "artifact/publish/published.csv")[0] == publication
        assert reviewer.json(flow + "artifact/publish/publication.json")["note"] == "Freigabe Zürich"

        flow = start_flow(operator)
        reviewer.call(flow + "input/Review/abort", {}, expected=(200, 302, 303))
        finish(admin, flow, "ABORTED", evidence)
        reviewer.call(flow + "artifact/publish/published.csv", expected=(404,))
        flow = start_flow(dual)
        dual.call(flow + "input/Review/proceed", form, expected=(200, 302, 303))
        assert "authenticated requester" in finish(admin, flow, "FAILURE", evidence)
        dual.call(flow + "artifact/publish/published.csv", expected=(404,))

    if args.examples:
        from io import BytesIO
        from zipfile import ZipFile
        municipalities = (data / "municipalities.csv").read_bytes()
        quality, _ = run(operator, "csv-quality-check", {"notes": "Zeile eins\nZeile zwei", "referenceDate": "2024-02-29"},
                         "SUCCESS", ("inputFile", "municipalities.csv", municipalities))
        report = operator.json(quality + "artifact/csv-quality-check/quality-report.json")
        assert report["notes"] == "Zeile eins\nZeile zwei" and report["referenceDate"] == "2024-02-29"
        assert report["records"] == 5 and report["invalidRecords"] == 3
        _, bad = run(operator, "csv-quality-check", {"referenceDate": "2025-02-29"},
                     "FAILURE", ("inputFile", "municipalities.csv", municipalities))
        assert "Invalid date parameter referenceDate" in bad
        archive = BytesIO()
        with ZipFile(archive, "w") as z:
            z.writestr("municipalities.csv", "id,name\n1001,Bern\n")
            z.writestr("population.csv", "id,population\n1001,150000\n")
        build, _ = run(operator, "zip-csv-import", {}, "SUCCESS", ("inputFile", "tables.zip", archive.getvalue()))
        assert b"1001,Bern,150000" in operator.call(build + "artifact/zip-csv-import/municipalities.csv")[0]
        for name, status in [("valid", "SUCCESS"), ("invalid", "FAILURE")]:
            build, _ = run(operator, "xtf-validation", {}, status,
                           ("inputFile", f"{name}.xtf", (data / f"{name}.xtf").read_bytes()))
            operator.json(build + "artifact/reports/xtf-validation.json")

    if args.large:
        for size in (5, 100):
            row = b"1234567890," + b"z" * 52 + b"\n"
            count = size * 1024 * 1024 // len(row)
            content = b"id,name\n" + row * count
            start = time.monotonic()
            build, _ = run(operator, "csv-analyzer", {}, "SUCCESS", ("inputFile", "large.csv", content))
            assert operator.json(build + "artifact/csv-analyzer/report.json")["records"] == count
            print(f"CSV {len(content)} bytes submit through result {time.monotonic() - start:.2f}s", flush=True)


if __name__ == "__main__":
    main()
