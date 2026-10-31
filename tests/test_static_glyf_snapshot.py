"""Fixed actual-only CSV fixtures; no Tk or live project/Global writes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import export_zhuyin_readings as decoder
import glyph_learning_service as learning


HEADER = "glyph_sha256,bopomofo,source_font,source_gid,verification,notes\r\n"
ROW = "a" * 64 + ',ㄅ,Fixture,7,v3.4 direct,"line 1\r\nline 2"\r\n'
RAW = ("\ufeff" + HEADER + ROW).encode("utf-8")
# Literal parsed assertion, deliberately independent of DictReader/_row_claim_ref.
CANONICAL = ('{"bopomofo":"ㄅ","glyph_sha256":"' + "a" * 64
             + '","notes":"line 1\\r\\nline 2","source_font":"Fixture",'
               '"source_gid":"7","verification":"v3.4 direct"}').encode("utf-8")


class StaticGlyfSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pa02-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / "static.csv"
        self.path.write_bytes(RAW)

    def expected(self):
        return {"a" * 64: {"bopomofo": "ㄅ", "source_font": "Fixture", "source_gid": 7,
            "verification": "v3.4 direct", "notes": "line 1\r\nline 2",
            "evidence_roots": ("static.csv:asset-sha256:" + hashlib.sha256(RAW).hexdigest()
                + ":row-sha256:" + hashlib.sha256(CANONICAL).hexdigest(),)}}

    def test_complete_output_preserves_bom_newlines_and_row_identity(self):
        self.assertEqual(decoder.load_verified_glyf_fingerprints(self.path), self.expected())

    def test_duplicate_conflict_invalid_and_missing_behavior(self):
        self.path.write_bytes(RAW + ROW.encode("utf-8"))
        result = decoder.load_verified_glyf_fingerprints(self.path)
        self.assertEqual(result["a" * 64]["bopomofo"], "ㄅ")
        expected = self.expected()["a" * 64]
        expected["evidence_roots"] = ("static.csv:asset-sha256:"
            + hashlib.sha256(RAW + ROW.encode("utf-8")).hexdigest()
            + ":row-sha256:" + hashlib.sha256(CANONICAL).hexdigest(),)
        self.assertEqual(result, {"a" * 64: expected})
        extra = (ROW.replace("ㄅ", "ㄆ") + ROW.replace("ㄅ", "ㄇ")
            + "bad,ㄅ,Fixture,7,v3.4 direct,invalid sha\r\n"
            + "b" * 64 + ",,Fixture,7,v3.4 direct,empty reading\r\n"
            + "c" * 64 + ",ㄅ,Fixture,7,v99 future,ineligible\r\n"
            + "d" * 64 + ",ㄈ,Fixture,bad,v4.9 direct,valid reading\r\n")
        self.path.write_bytes(RAW + extra.encode("utf-8"))
        result = decoder.load_verified_glyf_fingerprints(self.path)
        self.assertEqual(set(result), {"a" * 64, "d" * 64})
        self.assertEqual(result["a" * 64], {"bopomofo": "", "conflict_readings": ("ㄅ", "ㄆ")})
        self.assertEqual(result["d" * 64]["source_gid"], -1)
        self.assertEqual(decoder.load_verified_glyf_fingerprints(self.root / "absent.csv"), {})

    def test_parser_and_asset_hash_use_captured_bytes_when_path_changes(self):
        original = Path.read_bytes
        def read_then_replace(path):
            raw = original(path)
            if path == self.path:
                path.write_bytes(raw.replace("ㄅ".encode(), "ㄆ".encode()))
            return raw
        with patch.object(Path, "read_bytes", read_then_replace):
            result = decoder.load_verified_glyf_fingerprints(self.path)
        self.assertEqual(result, self.expected())
        self.assertEqual(self.path.stat().st_size, len(RAW))
        self.assertNotEqual(self.path.read_bytes(), RAW)

    def test_one_full_asset_hash_and_each_row_hash_independent_of_row_count(self):
        original = hashlib.sha256
        for count in (1, 8):
            raw = ("\ufeff" + HEADER + ROW * count).encode("utf-8")
            self.path.write_bytes(raw)
            inputs = []
            def track(data=b"", **kwargs):
                inputs.append(data)
                return original(data, **kwargs)
            with patch.object(decoder.hashlib, "sha256", track), patch.object(
                    decoder, "_sha256_file", side_effect=AssertionError("unexpected file rehash")):
                result = decoder.load_verified_glyf_fingerprints(self.path)
            self.assertEqual(len(result), 1)
            self.assertEqual(inputs.count(raw), 1)
            self.assertEqual(inputs.count(CANONICAL), count)
            self.assertEqual(len(inputs), count + 1)

    def learning_asset(self):
        path = self.root / learning.actual.STATIC_TTF_FINGERPRINT_FILE
        path.write_bytes(RAW)
        manifest = {"assets": [{"name": "ttf_verified_glyf_fingerprints", "chain": "actual",
            "path": path.name, "sha256": hashlib.sha256(RAW).hexdigest()}]}
        (self.root / "runtime_asset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_learning_retains_entry_and_exit_content_checks(self):
        path = self.learning_asset()
        reads = []
        original = Path.read_bytes
        def track(p):
            if p == path:
                reads.append(p)
            return original(p)
        with patch.object(learning.actual, "__file__", str(self.root / "actual_review.py")), \
                patch.object(Path, "read_bytes", track):
            static, ttf, cff, quarantine = learning._known_prompt_sources(None)
        self.assertEqual(len(reads), 3)  # entry + captured parse + exit
        self.assertEqual(static["a" * 64]["bopomofo"], "ㄅ")
        self.assertEqual((ttf, cff, quarantine), ({}, {}, {"ttf": set(), "cff": set()}))
        path.write_bytes(RAW.replace("ㄅ".encode(), "ㄆ".encode()))
        with patch.object(learning.actual, "__file__", str(self.root / "actual_review.py")), \
                self.assertRaisesRegex(ValueError, "static source SHA mismatch"):
            learning._known_prompt_sources(None)

    def test_learning_rejects_same_size_mutation_during_snapshot_read(self):
        path = self.learning_asset()
        original = Path.read_bytes
        reads = 0
        def mutate(p):
            nonlocal reads
            data = original(p)
            if p == path:
                reads += 1
                if reads == 2:
                    p.write_bytes(RAW.replace("ㄅ".encode(), "ㄆ".encode()))
            return data
        with patch.object(learning.actual, "__file__", str(self.root / "actual_review.py")), \
                patch.object(Path, "read_bytes", mutate), \
                self.assertRaisesRegex(ValueError, "static source changed while reading"):
            learning._known_prompt_sources(None)
        self.assertEqual(reads, 3)

    def test_decoder_uses_real_static_loader_for_exact_glyph(self):
        from tests.test_global_exact_glyph_read_reuse_v580 import (
            TTFWorkbookReadReuseIntegrationTests, _absent_snapshot)
        with patch.object(decoder, "DEFAULT_FINGERPRINTS", self.path):
            actual, audit = TTFWorkbookReadReuseIntegrationTests()._decode_fixture(
                self.root, _absent_snapshot(), name="static")
        self.assertEqual(actual["實際注音"], "ㄅ")
        self.assertEqual(actual["TTF字形SHA256"], "a" * 64)
        self.assertEqual(audit, [])


if __name__ == "__main__":
    unittest.main()
