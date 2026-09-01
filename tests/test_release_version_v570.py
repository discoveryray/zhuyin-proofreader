from __future__ import annotations

import json
import unittest
from pathlib import Path

import check_pronunciation_candidates
import export_zhuyin_readings
import review_gui
import runtime_source_validation
import standalone_gui
import standalone_proofread


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "5.7.0"


class ReleaseVersionConsistencyTests(unittest.TestCase):
    def test_current_release_metadata_is_consistently_v570(self):
        manifest = json.loads((ROOT / "runtime_asset_manifest.json").read_text(encoding="utf-8"))
        observed = {
            "VERSION.txt": (ROOT / "VERSION.txt").read_text(encoding="utf-8-sig").strip(),
            "standalone_proofread.VERSION": standalone_proofread.VERSION,
            "standalone_gui.VERSION": standalone_gui.VERSION,
            "check_pronunciation_candidates.VERSION": check_pronunciation_candidates.VERSION,
            "export_zhuyin_readings.VERSION": export_zhuyin_readings.VERSION,
            "runtime_source_validation.TOOL_VERSION": runtime_source_validation.TOOL_VERSION,
            "runtime_asset_manifest.tool_version": manifest.get("tool_version"),
            "review_gui imported VERSION": review_gui.VERSION,
        }
        for location, value in observed.items():
            with self.subTest(location=location):
                self.assertEqual(value, RELEASE_VERSION)


if __name__ == "__main__":
    unittest.main()
