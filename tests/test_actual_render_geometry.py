from pathlib import Path
import tempfile
import unittest

import fitz

from actual_render_geometry import (
    EXACT_VERTICAL_CFF_PDF, _direct_origin, _project_contours,
    exact_vertical_cff_location,
)


def rectangle(x0, y0, x1, y1):
    return [("moveTo", ((x0, y0),)), ("lineTo", ((x1, y0),)),
            ("lineTo", ((x1, y1),)), ("lineTo", ((x0, y1),)), ("closePath", ())]


class ActualRenderGeometryTests(unittest.TestCase):
    def test_projection_retains_every_annotation_contour_including_tone(self):
        recording = rectangle(0, 0, 900, 800) + rectangle(1050, 100, 1250, 600) + rectangle(1400, 0, 1500, 50)
        whole, annotation = _project_contours(recording, (100, 200), .02)
        self.assertEqual(whole, (100, 184, 130, 200))
        self.assertEqual(annotation, (121, 188, 130, 200))

    def test_crossing_or_absent_annotation_contours_fail_closed(self):
        for recording in (rectangle(0, 0, 1500, 800), rectangle(0, 0, 900, 800)):
            with self.subTest(recording=recording), self.assertRaises(ValueError):
                _project_contours(recording, (0, 0), .02)

    def test_vertical_tj_origin_uses_metrics_advance_and_page_matrix(self):
        content = b'/C0_0 1 Tf\n16.2992 0 0 16.2992 524.0551 644.8819 Tm\n[<0E0C>200<1261>200<16D8>]TJ'
        page_matrix = fitz.Matrix(1, 0, 0, -1, 0, 756.85)
        # Independent values from PDF text displacement equations, not source bbox.
        origins = [_direct_origin(content, gid, page_matrix) for gid in (3596, 4705, 5848)]
        for origin, expected in zip(origins, ((511.8307, 125.9854), (511.8307, 145.54444), (511.8307, 165.10348))):
            self.assertAlmostEqual(origin[0], expected[0], places=4)
            self.assertAlmostEqual(origin[1], expected[1], places=4)
        with self.assertRaisesRegex(ValueError, "非唯一"):
            _direct_origin(content + b'\n' + content, 4705, page_matrix)
        with self.assertRaisesRegex(ValueError, "text matrix"):
            _direct_origin(content.replace(b'16.2992', b'16.3000'), 4705, page_matrix)

    def test_exact_pdf_contract_cannot_be_claimed_for_different_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "unrelated.pdf"
            pdf.write_bytes(b"unrelated bytes")
            row = dict(pdf_sha256=EXACT_VERTICAL_CFF_PDF, physical_page=1, font_xref=994,
                       glyph_id=1693, occurrence_id="occ", review_id="review",
                       x0=511.122, y0=457.133, x1=511.122, y1=473.432,
                       source_record={"font_xref": 994, "glyph_id_字形索引": 1693,
                                      "CFF整字字形SHA256": "b764dfff0f2b0cc090121fa2e792f9de63a95724d64edea32af16d23026475f8"})
            with self.assertRaisesRegex(ValueError, "PDF bytes"):
                exact_vertical_cff_location(pdf, row, digest=EXACT_VERTICAL_CFF_PDF)
            with self.assertRaisesRegex(ValueError, "沒有適用"):
                exact_vertical_cff_location(pdf, row, digest="0" * 64)
