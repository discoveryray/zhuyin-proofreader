"""Actual-only, current-source checks before visual confirmation or reuse.

This is a bounded final-paint refusal check, not a pronunciation decoder or a
general PDF visibility interpreter. Unknown intersections cannot prove that a
target is visible. Results are transient and must be recomputed at write gates.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import fitz

from export_pdf_text_diagnostics import (
    TrueTypeGlyphInspector, build_font_lookup, normalize_basefont, resolve_font,
)
from cff_zhuyin_decoder import CFFZhuyinInspector, cff_glyph_outline_sha256

_BOX_KEYS = ("x0", "y0", "x1", "y1")
_LOCAL_PDF = "4dea642d96efbbe3aacd90e3e270f11fdb72e27078a6be6196660667cc32f9ef"
_LOCAL_OID = "occ_b0529c1e3c589091c6504826d3d5a33f3027ea6aa91b064ff3e8c9436a81fc28"
_LOCAL_RID = "rev_014c2c279c91d44af2f9c0197ebb8fdf48d5caa32fa72c547ecfa7794e04191b"
_LOCAL_SHA = "54ecb02b48caed9007d46c357a983827fe80413f3e6a1d343cc19c03a2cf1411"
_LOCAL_BOX = (252.822, 98.759, 284.711, 120.019)
_LOCAL_DRAWS = {
    414: ((277.31298828125, 99.77940368652344, 279.2130126953125, 101.67840576171875),
          (0.13669031858444214, 0.12195010483264923, 0.1252918243408203)),
    415: ((281.0549011230469, 107.43305969238281, 284.7688903808594, 111.14605712890625),
          (1.0, 1.0, 1.0)),
}
_LOCAL_STROKE_BOX = (38.264503479003906, 22.673498153686523,
                     299.0585021972656, 113.38949584960938)
_LOCAL_ROUND_RECT = (66.614501953125, 51.02349853515625,
                     270.70849609375, 85.03949737548828)


def _result(readable=False, donor=False, reason="SOURCE_UNPROVABLE", **metadata):
    return {"location_readable": readable, "donor_eligible": donor,
            "reason": reason, **metadata}


def _index(value):
    if isinstance(value, bool) or value is None or int(value) != float(value) or int(value) < 0:
        raise ValueError("SOURCE_INDEX_INVALID")
    return int(value)


def _tuple(entry):
    source = entry.get("source_record") or {}
    if not isinstance(source, dict):
        raise ValueError("SOURCE_RECORD_INVALID")
    aliases = {"physical_page": "實體頁碼", "char": "字元",
               "glyph_id": "glyph_id_字形索引", "zhuyin_component_id": "注音元件ID"}
    # Validate each supplied numeric claim before coalescing/comparing it.
    # Python bool is an int subclass (True == 1); that equality cannot prove
    # an independent root/component/font/page identity or coordinate binding.
    for key in ("physical_page", "font_xref", "glyph_id", "zhuyin_component_id", *_BOX_KEYS):
        for claim in (entry.get(key), source.get(aliases.get(key, key))):
            if claim is None or (key == "zhuyin_component_id" and claim == ""):
                continue
            if type(claim) not in (int, float) or not math.isfinite(claim):
                raise ValueError("SOURCE_NUMERIC_TYPE_INVALID:" + key)
            if key not in _BOX_KEYS:
                _index(claim)
    values = {}
    for key in ("pdf_sha256", "physical_page", "char", "font_xref", "glyph_id",
                "occurrence_id", "review_id", *_BOX_KEYS):
        other = source.get(aliases.get(key, key))
        value = entry.get(key)
        if value is not None and other is not None and value != other:
            raise ValueError("SOURCE_TUPLE_CONTRADICTION:" + key)
        values[key] = value if value is not None else other
    for key in ("pdf", "pdf_name", "font", "zhuyin_component_id"):
        value, other = entry.get(key), source.get(aliases.get(key, key))
        if key == "zhuyin_component_id":
            # Legacy workbook/export absence is None or empty text. This only
            # omits a component claim; it cannot prove root identity or reuse.
            value = None if value == "" else value
            other = None if other == "" else other
        if value is not None and other is not None and value != other:
            raise ValueError("SOURCE_TUPLE_CONTRADICTION:" + key)
    for key in ("occurrence_id", "review_id"):
        if not isinstance(values[key], str) or not values[key].strip():
            raise ValueError("SOURCE_ID_MISSING:" + key)
    if not isinstance(values["pdf_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", values["pdf_sha256"]):
        raise ValueError("SOURCE_PDF_SHA_MISSING")
    if not isinstance(values["char"], str) or len(values["char"]) != 1:
        raise ValueError("SOURCE_CHARACTER_INVALID")
    for key in ("physical_page", "font_xref", "glyph_id"):
        values[key] = _index(values[key])
    if values["physical_page"] < 1 or values["font_xref"] < 1:
        raise ValueError("SOURCE_PAGE_FONT_INVALID")
    bounds = tuple(float(values[k]) for k in _BOX_KEYS)
    if not all(math.isfinite(v) for v in bounds):
        raise ValueError("SOURCE_BBOX_INVALID")
    values["bbox"] = bounds
    return values, source


def _document_bytes(document):
    if document.is_closed or document.is_dirty:
        raise ValueError("SOURCE_DOCUMENT_UNSEALED")
    if document.stream is not None:
        return bytes(document.stream)
    # Reading document.name would seal current disk bytes, not the bytes the
    # already-open document cached. Only retained original stream bytes prove
    # that a caller's page belongs to the reviewed PDF snapshot.
    raise ValueError("SOURCE_DOCUMENT_BYTES_UNAVAILABLE")


def _exact_donor(document, values, source, entry):
    """Reprove project raw identity; Global independently applies its policy."""
    ttf, cff = source.get("TTF字形SHA256"), source.get("CFF整字字形SHA256")
    if ttf and cff:
        raise ValueError("SOURCE_EXACT_IDENTITY_CONTRADICTION")
    if not ttf and not cff:
        return False
    claimed = ttf or cff
    if not isinstance(claimed, str) or not re.fullmatch("[0-9a-f]{64}", claimed):
        raise ValueError("SOURCE_EXACT_SHA_INVALID")
    name, extension, _kind, raw = document.extract_font(values["font_xref"])
    if not raw:
        raise ValueError("SOURCE_FONT_BYTES_UNAVAILABLE")
    if ttf:
        if extension != "ttf":
            raise ValueError("SOURCE_NOT_TTF")
        component = source.get("注音元件ID")
        if component is None or component == "":
            component = entry.get("zhuyin_component_id")
        gid = values["glyph_id"] if component is None or component == "" else _index(component)
        inspector = TrueTypeGlyphInspector(raw)
        if gid != values["glyph_id"] and gid not in {c["gid"] for c in inspector.components(values["glyph_id"])}:
            raise ValueError("SOURCE_COMPONENT_NOT_ATTACHED")
        actual = inspector.glyph_sha256(gid)
    else:
        inspector = CFFZhuyinInspector(raw, normalize_basefont(name), {})
        if not source.get("CFF樣式群組") or inspector.style_group != source["CFF樣式群組"]:
            raise ValueError("SOURCE_CFF_STYLE_MISMATCH")
        actual = cff_glyph_outline_sha256(inspector._recording(values["glyph_id"]))
    if actual != claimed:
        raise ValueError("SOURCE_EXACT_SHA_MISMATCH")
    return True


def _finite_local_adapter(values, source, entry, seqno, intersections, drawings, bboxlog):
    """Exact original source, independently audited annotation-only overpaint.

    Exact PDF bytes seal the entire painting, font, clipping and resource state.
    This single adapter does not partition arbitrary Chinese glyphs by bbox.
    """
    if (values["pdf_sha256"] != _LOCAL_PDF or values["occurrence_id"] != _LOCAL_OID
            or values["review_id"] != _LOCAL_RID or values["physical_page"] != 4
            or values["bbox"] != _LOCAL_BOX or values["font_xref"] != 646
            or values["glyph_id"] != 19530 or values["char"] != "蔔" or seqno != 2
            or source.get("注音元件ID") != 14262 or source.get("TTF字形SHA256") != _LOCAL_SHA
            or entry.get("zhuyin_component_id") != 14262
            or {item[0] for item in intersections} != {*_LOCAL_DRAWS, 434, 442}
            or any(item[1] not in ("fill-path", "stroke-path") for item in intersections)):
        return False
    actual = [d for d in drawings if d.get("seqno") in _LOCAL_DRAWS]
    if len(actual) != 2:
        return False
    for drawing in actual:
        bounds, fill = _LOCAL_DRAWS[drawing["seqno"]]
        if (drawing.get("type") != "f" or drawing.get("fill_opacity") != 1.0
                or tuple(drawing["rect"]) != bounds or tuple(drawing.get("fill") or ()) != fill):
            return False
    # The frozen PDF draws the same smooth round-rectangle above the source
    # twice. Its actual path ends at y85; the generic miter-limit bbox extends
    # to y113. The independently audited joins/curves do not reach this source
    # (y98). This exception is ONLY for these exact PDF bytes and operations;
    # arbitrary stroke bbox intersections remain unprovable.
    borders = [d for d in drawings if d.get("seqno") in (432, 440)]
    if len(borders) != 2:
        return False
    for border in borders:
        if (border.get("type") != "fs" or tuple(border["rect"]) != _LOCAL_ROUND_RECT
                or border.get("width") != 2.8350000381469727
                or border.get("lineJoin") != 0.0 or border.get("lineCap") != (0, 0, 0)):
            return False
    if any(bboxlog[index][0] != "stroke-path" or tuple(bboxlog[index][1]) != _LOCAL_STROKE_BOX
           for index in (434, 442)):
        return False
    return True


def source_visual_safety(entry, *, pdf=None, document=None, page=None):
    """Return current source safety, refusing missing, stale or unknown proof.

    ``pdf`` can be a mapped path or original PDF bytes. Supplied fitz snapshots
    must retain identical original bytes and be unmodified. Caller flags never
    authorize location/reuse. The owner must still seal files through commit.
    """
    owned = None
    try:
        values, source = _tuple(entry)
        if page is not None:
            if document is None:
                document = page.parent
            if page.parent is not document or page.number != values["physical_page"] - 1:
                raise ValueError("SOURCE_PAGE_OWNERSHIP_MISMATCH")
        if pdf is not None:
            raw = bytes(pdf) if isinstance(pdf, (bytes, bytearray)) else Path(pdf).read_bytes()
        elif document is not None:
            raw = _document_bytes(document)
        else:
            path = entry.get("pdf") or source.get("pdf")
            if not path:
                raise ValueError("SOURCE_PDF_MISSING")
            raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != values["pdf_sha256"]:
            raise ValueError("SOURCE_PDF_SHA_MISMATCH")
        if document is not None and _document_bytes(document) != raw:
            raise ValueError("SOURCE_DOCUMENT_BYTES_MISMATCH")
        # PyMuPDF's trace/bbox readers temporarily change rotation and mark a
        # rotated document dirty. Read an owned copy of the proved bytes so a
        # read cannot mutate/retire the caller's sealed snapshot. No caller can
        # concurrently change this private document.
        owned = document = fitz.open(stream=raw, filetype="pdf")
        page = document[values["physical_page"] - 1]
        target = fitz.Rect(values["bbox"])
        if target.is_empty:
            from actual_render_geometry import EXACT_VERTICAL_CFF_PDF, exact_vertical_cff_location
            if values["pdf_sha256"] != EXACT_VERTICAL_CFF_PDF:
                raise ValueError("SOURCE_BBOX_EMPTY")
            path = pdf if isinstance(pdf, (str, Path)) else entry.get("pdf")
            target = fitz.Rect(exact_vertical_cff_location(Path(path), entry, digest=values["pdf_sha256"])["whole"])
        if not (page.rect * page.derotation_matrix).contains(target):
            raise ValueError("SOURCE_BBOX_OUTSIDE_PAGE")
        lookup = build_font_lookup(page.get_fonts(full=True))
        traces = page.get_texttrace()
        matches = []
        for span in traces:
            xref, *_ = resolve_font(lookup, normalize_basefont(span.get("font")))
            if xref != values["font_xref"]:
                continue
            for char in span.get("chars", ()):
                if (char[0] == ord(values["char"]) and char[1] == values["glyph_id"]
                        and all(abs(float(a)-b) <= .001 for a, b in zip(char[3], values["bbox"]))):
                    matches.append((span, char))
        if len(matches) != 1:
            raise ValueError("SOURCE_DRAW_NOT_UNIQUE")
        span, _char = matches[0]
        if span.get("type") not in (0, 1) or span.get("opacity", 0) <= 0:
            raise ValueError("SOURCE_DRAW_NOT_VISIBLE")
        seqno = _index(span.get("seqno"))
        # A single text-show operation can overprint two different characters
        # in one slot. Their seqno is equal; this is still contradictory target
        # evidence. Ordinary adjacent glyph bbox overlap is not a same slot.
        for other in traces:
            if other.get("seqno", -1) < seqno or other.get("type") == 3:
                continue
            xref, *_ = resolve_font(lookup, normalize_basefont(other.get("font")))
            for char in other.get("chars", ()):
                if (all(abs(float(a)-b) <= .001 for a, b in zip(char[3], values["bbox"]))
                        and (char[0] != ord(values["char"]) or char[1] != values["glyph_id"]
                             or xref != values["font_xref"])):
                    raise ValueError("SOURCE_SLOT_CONTRADICTION")
        exact = _exact_donor(document, values, source, entry)
        intersections = []
        # Text bboxlog entries span whole lines; test individual characters to
        # avoid treating unrelated later text on the same line as an occluder.
        for later in traces:
            if later.get("seqno", -1) <= seqno or later.get("type") == 3:
                continue
            for char in later.get("chars", ()):
                if target.intersects(fitz.Rect(char[3])):
                    intersections.append((later["seqno"], "text"))
        bboxlog = page.get_bboxlog()
        for index, (kind, bounds, *_rest) in enumerate(bboxlog):
            if index > seqno and kind not in ("fill-text", "stroke-text", "ignore-text"):
                if target.intersects(fitz.Rect(bounds)):
                    intersections.append((index, kind))
        if intersections:
            if exact and _finite_local_adapter(values, source, entry, seqno, intersections, page.get_drawings(), bboxlog):
                return _result(True, False, "EXACT_ANNOTATION_OVERPAINT_LOCAL_ONLY", source_seqno=seqno)
            return _result(reason="LATER_PAINT_SOURCE_UNPROVABLE", source_seqno=seqno)
        return _result(True, exact, "SOURCE_DRAW_UNCONTRADICTED" if exact else "SOURCE_LOCATION_ONLY_NO_EXACT_PROOF",
                       source_seqno=seqno)
    except Exception as exc:
        return _result(reason=str(exc) or type(exc).__name__)
    finally:
        if owned is not None:
            owned.close()
