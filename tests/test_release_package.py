"""Finite candidate packaging and main evidence regressions; no Tk or real DB."""
import hashlib
import json
import os
import posixpath
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import re
from urllib.parse import unquote, urlsplit
import zipfile

from scripts import build_release_package as package
from scripts import release_ci_evidence as evidence
from tests.test_ci_validation_workflow import action, job, step

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "151b90f6f5f2a352e828fa6f73402dfb8c4f5617"


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.data = {"entry.py": b"import helper\n", "helper.py": b"VALUE = 1\n",
                     "asset.csv": b"header\nrow\n", "seed.csv": b"empty_header\n",
                     "VERSION.txt": b"5.8.0\n", "excluded-personal.sqlite3": b"not distributable"}
        self.spec = {"schema": "zhuyin-release-allowlist/1", "version": "5.8.0", "files": [
            {"path": "entry.py", "role": "runtime_module"}, {"path": "helper.py", "role": "runtime_module"},
            {"path": "asset.csv", "role": "runtime_asset"},
            {"path": "seed.csv", "role": "builtin_seed", "baseline_sha256": hashlib.sha256(self.data["seed.csv"]).hexdigest()},
            {"path": "VERSION.txt", "role": "metadata"}, {"path": "runtime_asset_manifest.json", "role": "metadata"}]}
        self.data["runtime_asset_manifest.json"] = json.dumps({"tool_version": "5.8.0", "assets": [{"path": "asset.csv", "sha256": hashlib.sha256(self.data["asset.csv"]).hexdigest()}]}).encode()
        self.save()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT).decode().strip()

    def save(self):
        for path, raw in self.data.items():
            (self.root / path).write_bytes(raw)
        (self.root / "release").mkdir(exist_ok=True)
        (self.root / package.ALLOWLIST).write_text(json.dumps(self.spec), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic package fixture")
        self.sha = self.git("rev-parse", "HEAD")

    def test_exact_blobs_checksums_exclusion_and_reproducible_zip(self):
        first = package.build(self.root, self.sha, Path(self.temp.name) / "one")
        second = package.build(self.root, self.sha, Path(self.temp.name) / "two")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            expected = {item["path"] for item in self.spec["files"]}
            self.assertEqual(set(archive.namelist()), expected | {"package-manifest.json"})
            manifest = json.loads(archive.read("package-manifest.json"))
            self.assertEqual(manifest["source_commit"], self.sha)
            self.assertEqual(manifest["source_tree"], self.git("rev-parse", "HEAD^{tree}"))
            self.assertEqual(manifest["status"], "CANDIDATE_NOT_RELEASED")
            for item in manifest["files"]:
                raw = archive.read(item["path"])
                self.assertEqual(raw, package.git(self.root, "show", self.sha + ":" + item["path"]))
                self.assertEqual(item["bytes"], len(raw))
                self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(first.with_name(first.name + ".sha256").read_text().split()[0], hashlib.sha256(first.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            package.build(self.root, self.sha, first.parent)

    def test_dirty_checkout_and_inexact_or_wrong_commit_fail_before_output(self):
        with self.assertRaisesRegex(ValueError, "exact"):
            package.prepare(self.root, "HEAD")
        with self.assertRaisesRegex(ValueError, "HEAD"):
            package.prepare(self.root, "0" * 40)
        (self.root / "seed.csv").write_bytes(b"personal later rows\n")
        with self.assertRaisesRegex(ValueError, "clean"):
            package.build(self.root, self.sha, Path(self.temp.name) / "absent")
        self.assertFalse((Path(self.temp.name) / "absent").exists())

    def test_missing_runtime_dependency_is_rejected(self):
        self.spec["files"] = [item for item in self.spec["files"] if item["path"] != "helper.py"]
        self.save()
        with self.assertRaisesRegex(ValueError, "dependency missing"):
            package.prepare(self.root, self.sha)

    def test_changed_seed_and_asset_are_rejected(self):
        self.data["seed.csv"] = b"personal rows\n"
        self.save()
        with self.assertRaisesRegex(ValueError, "seed changed"):
            package.prepare(self.root, self.sha)
        self.data["seed.csv"] = b"empty_header\n"
        self.data["asset.csv"] = b"pollution\n"
        self.save()
        with self.assertRaisesRegex(ValueError, "asset integrity"):
            package.prepare(self.root, self.sha)

    def test_missing_unsafe_and_duplicate_paths_are_rejected(self):
        original = self.spec["files"][:]
        for item, reason in [({"path": "absent.py", "role": "runtime_module"}, "missing"),
                             ({"path": "../secret", "role": "metadata"}, "invalid"),
                             ({"path": "ENTRY.py", "role": "runtime_module"}, "duplicate")]:
            self.spec["files"] = original + [item]
            self.save()
            with self.subTest(path=item["path"]), self.assertRaisesRegex(ValueError, reason):
                package.prepare(self.root, self.sha)

    def test_asset_roster_unknown_role_and_version_fail_closed(self):
        self.spec["files"] = [item for item in self.spec["files"] if item["path"] != "asset.csv"]
        self.save()
        with self.assertRaisesRegex(ValueError, "roster"):
            package.prepare(self.root, self.sha)
        self.spec["files"].append({"path": "asset.csv", "role": "unknown"})
        self.save()
        with self.assertRaisesRegex(ValueError, "unknown package role"):
            package.prepare(self.root, self.sha)
        self.spec["files"][-1]["role"] = "runtime_asset"
        self.data["VERSION.txt"] = b"9.0.0\n"
        self.save()
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            package.prepare(self.root, self.sha)

    def test_output_inside_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            package.build(self.root, self.sha, self.root / "candidate")


class ReleaseEvidenceTests(unittest.TestCase):
    def test_delivered_document_local_links_resolve_inside_allowlist(self):
        spec = json.loads((ROOT / package.ALLOWLIST).read_text(encoding="utf-8"))
        included = {item["path"] for item in spec["files"]}
        for document in ("README.md", "docs/USER_GUIDE.md", "docs/UPGRADE_V580.md", "docs/RELEASE_ACCEPTANCE_V580.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                parsed = urlsplit(link)
                if parsed.scheme or not parsed.path:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(parsed.path)))
                with self.subTest(document=document, target=target):
                    self.assertIn(target, included)

    def test_runtime_assets_and_manifest_other_fields_unchanged_from_baseline(self):
        original = json.loads(package.git(ROOT, "show", BASELINE + ":runtime_asset_manifest.json"))
        current = json.loads((ROOT / "runtime_asset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(current["tool_version"], "5.8.0")
        current["tool_version"] = original["tool_version"]
        self.assertEqual(current, original)
        self.assertEqual(len(original["assets"]), 20)
        for asset in original["assets"]:
            with self.subTest(path=asset["path"]):
                self.assertEqual((ROOT / asset["path"]).read_bytes(), package.git(ROOT, "show", BASELINE + ":" + asset["path"]))

    def test_release_runtime_lock_is_exact_ci_subset_without_test_tools(self):
        def pins(name):
            return {line.strip().lower() for line in (ROOT / name).read_text().splitlines() if line.strip() and not line.startswith("#")}
        expected = {"pip==24.2", "pymupdf==1.26.7", "openpyxl==3.1.5", "fonttools==4.63.0", "python-docx==1.2.0", "et_xmlfile==2.0.0", "lxml==6.1.3", "typing_extensions==4.16.0"}
        self.assertEqual(pins("requirements-release-lock.txt"), expected)
        self.assertTrue(expected <= pins("requirements-ci-lock.txt"))

    def test_child_failure_retains_original_output_and_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "child.log"
            code = evidence.run(output, [sys.executable, "-c", "import sys;print('original child output');sys.exit(13)"])
            self.assertEqual(code, 13)
            self.assertIn("original child output", output.read_text())
            self.assertIn("exit_code=13", output.read_text())

    def test_main_installer_actual_powershell_branch_and_failure_propagation(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        for name, expected_main, expected_legacy in [
            ("Upgrade pip", "pip==24.2", "--upgrade"),
            ("Install development dependencies", "requirements-ci-lock.txt", "requirements-ci.txt"),
            ("Run full pytest suite", "scripts/release_ci_evidence.py", "-m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml")]:
            body = action(step(job("test"), name))[1]
            for is_main in (True, False):
                for code in (0, 17):
                    driver = "function python { $args -join ' '; $global:LASTEXITCODE = [int]$env:FIXTURE_EXIT }\n" + body
                    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", driver], capture_output=True,
                        env={**os.environ, "GITHUB_EVENT_NAME": "push" if is_main else "pull_request", "GITHUB_REF": "refs/heads/main" if is_main else "refs/pull/29/merge", "FIXTURE_EXIT": str(code)}, timeout=30)
                    with self.subTest(step=name, main=is_main, code=code):
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertIn(expected_main if is_main else expected_legacy, result.stdout.decode())
                        if not is_main:
                            self.assertNotIn("release_ci_evidence.py", result.stdout.decode())

    def test_main_upload_has_finite_failure_time_originals(self):
        upload = step(job("test"), "Upload main full evidence")
        self.assertIn("always() && github.event_name == 'push' && github.ref == 'refs/heads/main'", upload)
        for path in ("tmp/ci-test-inventory.json", "tmp/ci-pytest.xml", "tmp/main-full/environment.json", "tmp/main-full/pytest.log"):
            self.assertIn(path, upload)
        self.assertNotIn("**", upload)
        self.assertNotIn("continue-on-error", upload)
        self.assertIn("overwrite: false", upload)
        self.assertIn("include-hidden-files: false", upload)


if __name__ == "__main__":
    unittest.main()
