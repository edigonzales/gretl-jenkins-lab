#!/usr/bin/env python3
"""Regression checks for the committed V2 catalog and Job DSL generator."""
import importlib.util
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

spec = importlib.util.spec_from_file_location("generator", Path(__file__).with_name("generate-jobs.py"))
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = Path(directory.name)
        subprocess.run(["git", "init", "-q", self.repository], check=True)
        for name in ("sample", "internal"):
            path = self.repository / "jobs" / name
            path.mkdir(parents=True)
            (path / "job.groovy").write_text("defaults 'run'\n")
            (path / "job.yaml").write_text(f"""schemaVersion: 2
id: {name}
name: Sample
description: A sample
runtime: gretl
script: job.groovy
exposed: {str(name == 'sample').lower()}
""")

    def commit(self):
        command = ["git", "-C", str(self.repository)]
        subprocess.run(command + ["add", "."], check=True)
        subprocess.run(command + ["-c", "user.name=Tests", "-c", "user.email=tests@example.com",
                                  "commit", "-qm", "fixture"], check=True)
        return generator.git(self.repository, "rev-parse", "HEAD")

    def append(self, value):
        with (self.repository / "jobs/sample/job.yaml").open("a") as stream:
            stream.write(value)

    def test_pin_and_exposed(self):
        revision = self.commit()
        self.append("unexpected: key\n")
        catalog = generator.read_catalog(self.repository, revision)
        self.assertEqual(["sample"], [d["id"] for d in catalog.values() if d["exposed"]])
        with self.assertRaisesRegex(ValueError, "unexpected"):
            generator.read_catalog(self.repository, self.commit())

    def test_missing_successor(self):
        self.append("onSuccess: {job: missing}\n")
        with self.assertRaisesRegex(ValueError, "onSuccess"):
            generator.read_catalog(self.repository, self.commit())

    def test_duplicate_key(self):
        self.append("id: another\n")
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            generator.read_catalog(self.repository, self.commit())

    def test_output_traversal(self):
        self.append("outputs: [{name: invalid, label: Invalid, path: ../oops, parameter: output}]\n")
        with self.assertRaisesRegex(ValueError, "invalid output"):
            generator.read_catalog(self.repository, self.commit())

    def test_cron(self):
        generator.validate_cron("H H(1-3) * * *")
        for cron in ["61 * * * *", "H/0 * * * *", "H(4-3) * * * *", "H 00-35 * * *"]:
            with self.subTest(cron=cron), self.assertRaises(ValueError):
                generator.validate_cron(cron)

    def test_default_and_enum_order(self):
        p = {"name": "delimiter", "label": "Trennzeichen", "type": "enum", "default": ";",
             "options": [{"value": ",", "label": "Komma"}, {"value": ";", "label": "Semikolon"}]}
        self.assertEqual(generator.choices(p), [";", ","])
        with self.assertRaisesRegex(ValueError, "invalid date"):
            generator.check_value({"name": "date", "type": "date"}, "2025-02-29")

    def test_determinism(self):
        revision = self.commit()
        catalog = generator.read_catalog(self.repository, revision)
        first = generator.render(catalog["sample"], catalog, "https://github.com/example/jobs",
                                 revision, "runtime:local", "println '@MODEL@'")
        second = generator.render(catalog["sample"], catalog, "https://github.com/example/jobs",
                                  revision, "runtime:local", "println '@MODEL@'")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
