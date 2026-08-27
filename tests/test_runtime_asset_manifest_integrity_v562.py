from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from runtime_source_validation import REQUIRED_ASSET_CHAINS, validate_asset_manifest


ROOT = Path(__file__).resolve().parents[1]

# SHA-256 of the exact Git blob payload after normalizing CRLF back to LF.
# These values were identical at the v5.6.1 import (005ccf0), daa1d5a,
# 448931d, 8585810, and 93edd71.  They prove that the repair changes no CSV
# field, row, ordering, BOM, or provenance text; it only restores the CRLF
# bytes already approved by runtime_asset_manifest.json.
HISTORICAL_LF_SHA256 = {
    "統一用字手冊_注音規則.csv": "4001c0bc802b13d617ce2fb665c502e7166ea23a1bc221d59f1947ff9fc41917",
    "統一用字手冊_字音限制.csv": "121e8e933e1ae77466117659d820ec156e15271fedcb1225a283b056faa7a496",
    "character_overrides.csv": "6d1db8e7c996c42c87d4c2808d5481d0bdbecbd17e11956e528d20f73d864539",
    "source_context_overrides.csv": "a854736eb8505ca0bb7de17ea3de8b9548acf9d3dbc4d9f840a0ca40bcd7071f",
    "zhuyin_component_map.csv": "590c3bb57cb0380ebc8ce74626822b36e36afa6811ec9cc645c1ea904ac57266",
    "font_compatibility_groups.csv": "41451fc0f1b8887fd1ae54babb0b02958aca760384b308b74c6ffb05bae82298",
    "cff_bopomofo_symbol_map.csv": "8e7fbf3eb026191e5525e538e514432fae7f7557e7a9ba851f26597d16203ebc",
    "cff_crossfamily_cid_consensus.csv": "f442959c602c7260d7ca8841426498cdf4ad43081305d1f81e5b648e75529085",
    "ttf_xref_component_overrides.csv": "0b5e19844b8983d6bb52aa4337a085f99968e480c280849fcc7cd53a1b3b7a60",
    "ttf_verified_component_transforms.csv": "deb468a1891f8665a177619168f8ab6133bf911a3631a0a6e099db9aa473b2da",
    "ttf_verified_glyf_fingerprints.csv": "9a9b22a8d6e67a517daf556240b6fad08e7e3154c882758e36b055c2be9c1a05",
    "ttf_verified_outline_signatures.csv": "c5302d57af7b0fa5d97f38be0b183c8942e5e1c06e3bcc4e8e8c7c175862f014",
    "ttf_verified_symbol_templates.csv": "3821fb4d82191469ac9334a9527817d081d2eed55607092d5660ac7a2f5ab9c8",
    "structural_detection_exclusions.csv": "9e39590beb216bccb3e99837c1b74696c1b502713ba4afebfa8dfaae718b3bbc",
}


class RuntimeAssetManifestIntegrityV562Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            (ROOT / "runtime_asset_manifest.json").read_text(encoding="utf-8")
        )
        cls.assets_by_path = {
            str(asset["path"]): asset for asset in cls.manifest["assets"]
        }

    def test_production_runtime_root_validates_without_exceptions(self):
        report = validate_asset_manifest(ROOT)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])
        self.assertTrue(report["required_asset_roster_ok"])
        self.assertEqual(
            report["required_asset_chain_roster_ok"],
            {"actual": True, "expected": True},
        )
        self.assertEqual(len(report["assets"]), len(REQUIRED_ASSET_CHAINS))
        for asset in report["assets"]:
            self.assertTrue(asset["ok"], (asset["name"], asset["errors"]))
            schema = asset.get("schema") or {}
            self.assertTrue(schema.get("ok"), (asset["name"], schema.get("errors")))

    def test_repaired_assets_are_identical_to_historical_rows_after_eol_normalization(self):
        self.assertEqual(set(HISTORICAL_LF_SHA256), set(HISTORICAL_LF_SHA256) & set(self.assets_by_path))
        for relative, historical_lf_sha in HISTORICAL_LF_SHA256.items():
            with self.subTest(asset=relative):
                payload = (ROOT / relative).read_bytes()
                spec = self.assets_by_path[relative]
                self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
                self.assertIn(b"\r\n", payload)
                self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
                self.assertEqual(hashlib.sha256(payload).hexdigest(), spec["sha256"])
                normalized = payload.replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(normalized).hexdigest(), historical_lf_sha)

    def test_git_attributes_freeze_every_manifest_csv_as_raw_bytes(self):
        pinned = set()
        for raw_line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            pattern, *attributes = line.split()
            if "-text" in attributes:
                pinned.add(pattern.removeprefix("/"))
        manifest_csvs = {
            str(asset["path"])
            for asset in self.manifest["assets"]
            if str(asset.get("kind") or "").lower() == "csv"
        }
        self.assertEqual(pinned, manifest_csvs)

    def test_manifest_chain_roster_is_unchanged(self):
        observed = {
            str(asset["name"]): str(asset["chain"])
            for asset in self.manifest["assets"]
        }
        self.assertEqual(observed, REQUIRED_ASSET_CHAINS)


if __name__ == "__main__":
    unittest.main()
