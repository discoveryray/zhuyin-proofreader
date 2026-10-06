"""Finite crop-only adapter for three independently located vertical CFF draws.

The exact PDF/content/font hashes freeze the audited page graphics/text states,
direct resource scope, clipping, Forms and matrices. This is not a general PDF
interpreter or actual pronunciation evidence. Unknown documents fail closed.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import fitz

from cff_zhuyin_decoder import (
    ANNOTATION_X_MIN, CFFZhuyinInspector, _bounds, _split_contours,
    cff_glyph_outline_sha256,
)

EXACT_VERTICAL_CFF_PDF = "79a7e0c3d870e52161d309e0953a9fc7048ef6e23a872b5550479ae9c0b1bd4b"
_CONTENT_SHA = "66d63cb05d5ed89ccff31ad3642c3c4989e97e673144b4ac9fad97c95448dbb5"
_FONT_SHA = "4ff70e24391fe1c7f9807407ee2cd72934c392cbf87b09e15199fcb56c9014bd"
# Values are source evidence, not replacement rectangles. Draw position is
# derived below from the actual Tm/TJ bytes, vertical metrics and full contours.
_DRAWS = {
    1693: ((511.122, 457.133, 511.122, 473.432), "b764dfff0f2b0cc090121fa2e792f9de63a95724d64edea32af16d23026475f8"),
    4705: ((511.831, 131.574, 511.831, 147.873), "3d69f6716897ba2aea624ec8586918f68ea09ecdce4e1c79586c1c36fb706ed5"),
    5848: ((511.831, 151.133, 511.831, 167.432), "8fb9d7a3f2dc8a276d4f38e900a64be4d125c223025fc656725f4e123cbb23a0"),
}


def _project_contours(recording, origin, scale):
    """Project every contour/control point, retaining all tone/tiny contours."""
    boxes = [_bounds(contour) for contour in _split_contours(recording)]
    if not boxes or any(box is None for box in boxes):
        raise ValueError("CFF 輪廓不完整")
    left = [box for box in boxes if box[2] < ANNOTATION_X_MIN]
    right = [box for box in boxes if box[0] >= ANNOTATION_X_MIN]
    if not left or not right or len(left) + len(right) != len(boxes):
        raise ValueError("CFF 漢字／完整注音輪廓無法完全分離")
    def project(selected):
        x0, y0 = min(b[0] for b in selected), min(b[1] for b in selected)
        x1, y1 = max(b[2] for b in selected), max(b[3] for b in selected)
        result = (origin[0] + scale*x0, origin[1] - scale*y1,
                  origin[0] + scale*x1, origin[1] - scale*y0)
        if not all(math.isfinite(v) for v in result):
            raise ValueError("CFF 投影座標無效")
        return result
    return project(boxes), project(right)


def _direct_origin(content, gid, page_matrix):
    candidates = []
    shows = re.findall(rb"/C0_0 1 Tf\s+([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) Tm\s+\[(.*?)\]TJ", content)
    for a, d, e, f, array in shows:
        if float(a) != 16.2992 or a != d:
            raise ValueError("exact-PDF geometry text matrix 不符")
        offset = 0.0
        for match in re.finditer(rb"<([0-9A-F]+)>|(-?\d+(?:\.\d+)?)", array):
            if match[2]:
                offset -= float(match[2]) / 1000
                continue
            codes = bytes.fromhex(match[1].decode("ascii"))
            if len(codes) % 2:
                raise ValueError("exact-PDF geometry CID bytes 不符")
            for index in range(0, len(codes), 2):
                cid = int.from_bytes(codes[index:index+2], "big")
                if cid == gid:
                    tm = fitz.Matrix(float(a), 0, 0, float(d), float(e), float(f))
                    origin = fitz.Point(-.75, offset-.86) * tm * page_matrix
                    candidates.append((origin.x, origin.y))
                offset -= 1.0
    if len(candidates) != 1:
        raise ValueError("exact-PDF geometry 非唯一直接繪製")
    return candidates[0]


def exact_vertical_cff_location(pdf: Path, entry, *, digest: str):
    if digest != EXACT_VERTICAL_CFF_PDF or entry.get("pdf_sha256") != digest:
        raise ValueError("沒有適用的 exact-PDF geometry contract")
    gid = int(entry.get("glyph_id") or -1)
    source = entry.get("source_record") or {}
    if (entry.get("physical_page") != 1 or entry.get("font_xref") != 994
            or gid not in _DRAWS or not entry.get("occurrence_id") or not entry.get("review_id")):
        raise ValueError("exact-PDF geometry occurrence／頁／fontxref／CID 不符")
    expected_bbox, expected_outline = _DRAWS[gid]
    bounds = tuple(float(entry[k]) for k in ("x0", "y0", "x1", "y1"))
    if bounds != expected_bbox or source.get("CFF整字字形SHA256") != expected_outline:
        raise ValueError("exact-PDF geometry 原 bbox／完整輪廓 SHA 不符")
    if source.get("font_xref") != 994 or int(source.get("glyph_id_字形索引") or -1) != gid:
        raise ValueError("exact-PDF geometry source_record fontxref／CID 不符")
    pdf_bytes = Path(pdf).read_bytes()
    if hashlib.sha256(pdf_bytes).hexdigest() != digest:
        raise ValueError("exact-PDF geometry PDF bytes 在預檢後變動")
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        direct = [font for font in page.get_fonts(full=True) if font[0] == 994 and font[4] == "C0_0" and font[6] == 0]
        if len(direct) != 1 or direct[0][5] != "Identity-V" or page.rotation != 0:
            raise ValueError("exact-PDF geometry direct resource／rotation 不符")
        if page.get_contents() != [2]:
            raise ValueError("exact-PDF geometry content scope 不符")
        content = doc.xref_stream(2)
        font_data = doc.extract_font(994)[3]
        if hashlib.sha256(content).hexdigest() != _CONTENT_SHA or hashlib.sha256(font_data).hexdigest() != _FONT_SHA:
            raise ValueError("exact-PDF geometry content／font SHA 不符")
        if doc.xref_get_key(994, "Subtype") != ("name", "/Type0") or doc.xref_get_key(994, "Encoding") != ("name", "/Identity-V"):
            raise ValueError("exact-PDF geometry Type0／Identity-V 不符")
        if (doc.xref_get_key(994, "DescendantFonts") != ("xref", "1003 0 R")
                or re.sub(r"\s+", "", doc.xref_object(1003)) != "[10240R]"
                or doc.xref_get_key(1024, "Subtype") != ("name", "/CIDFontType0")):
            raise ValueError("exact-PDF geometry CID descendant 不符")
        if doc.xref_get_key(1024, "DW2") != ("array", "[860 -1000]") or doc.xref_get_key(1024, "W2")[0] != "null":
            raise ValueError("exact-PDF geometry vertical metrics 不符")
        widths = doc.xref_get_key(1024, "W")[1]
        if not re.search(rf"(?<!\d){gid}\s*\[\s*1500\s*\]", widths):
            raise ValueError("exact-PDF geometry CID width 不符")
        inspector = CFFZhuyinInspector(font_data, "", {})
        if (list(inspector.top.FontMatrix) != [.001, 0, 0, .001, 0, 0]
                or len(inspector.top.FDArray) != 1 or "FontMatrix" in inspector.top.FDArray[0].rawDict):
            raise ValueError("exact-PDF geometry font matrix 不符")
        recording = inspector._recording(gid)
        if cff_glyph_outline_sha256(recording) != expected_outline:
            raise ValueError("exact-PDF geometry 完整 CFF 輪廓不符")
        # Hash-bound content has exactly two /C0_0 text shows, no alternate text
        # states. The audited CTM is identity at both; unrelated Forms cannot
        # redefine these direct page text resources in this exact document.
        origin = _direct_origin(content, gid, page.transformation_matrix)
        painted = []
        for span in page.get_texttrace():
            for char in span["chars"]:
                if char[1] == gid and tuple(round(v, 3) for v in char[3]) == bounds:
                    if span["wmode"] == 1 and max(abs(char[2][axis]-origin[axis]) for axis in (0, 1)) <= .00004:
                        painted.append(char)
        if len(painted) != 1:
            raise ValueError("exact-PDF geometry trace／原 bbox／origin 非唯一綁定")
        whole, annotation = _project_contours(recording, origin, 16.2992*.001)
        padded = fitz.Rect(annotation) + (-.25, -.25, .25, .25)
        # Other same-program zero-width trace boxes need their full outlines;
        # ordinary nondegenerate trace boxes are sufficient for crop exclusion.
        for span in page.get_texttrace():
            for char in span["chars"]:
                if char == painted[0]:
                    continue
                other = fitz.Rect(char[3])
                if other.is_empty and span["wmode"] == 1 and abs(span["size"]-16.2992) < .00004:
                    try:
                        other = fitz.Rect(_project_contours(inspector._recording(char[1]), char[2], 16.2992*.001)[0])
                    except Exception as exc:
                        raise ValueError("exact-PDF geometry 鄰近零寬字形無法核對") from exc
                if not other.is_empty and padded.intersects(other):
                    raise ValueError("exact-PDF geometry 注音裁圖混入其他字形")
        if not page.rect.contains(padded) or not page.rect.contains(fitz.Rect(whole)):
            raise ValueError("exact-PDF geometry 投影超出頁面")
        return {"whole": whole, "annotation": tuple(padded), "origin": origin,
                "contract": "exact-vertical-cff-three-draws/1", "content_sha256": _CONTENT_SHA,
                "font_sha256": _FONT_SHA, "glyph_sha256": expected_outline}
