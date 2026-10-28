"""Isolated genuine glyph/PDF sources for legacy actual transaction fixtures.

Seed strings only choose fixture contours. Every returned exact key is computed
from real embedded raw glyf bytes, and every locator comes from its saved PDF.
No production proof is replaced, and no test material becomes a runtime asset.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
from pathlib import Path

import fitz
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from export_pdf_text_diagnostics import TrueTypeGlyphInspector, normalize_basefont

_GLYPHS = {}
_SEEDS = {}
_TEMP = None
_CACHE = {}


def _font(glyph, characters):
    builder = FontBuilder(1000, isTTF=True)
    names = [".notdef", "fixture"]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({ord(char): "fixture" for char in characters})
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "fixture": copy.deepcopy(glyph)})
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    family = "ActualSourceFixture" + hashlib.sha256(repr(list(glyph.coordinates)).encode()).hexdigest()[:12]
    builder.setupNameTable({"familyName": family, "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    stream = io.BytesIO(); builder.save(stream)
    return stream.getvalue()


def register_fixture_glyph(seed):
    seed = str(seed)
    if seed not in _SEEDS:
        digest = hashlib.sha256(seed.encode()).digest()
        pen = TTGlyphPen(None)
        width, height = 220 + digest[0], 350 + digest[1]
        pen.moveTo((0, 0)); pen.lineTo((width, 0))
        pen.lineTo((width, height)); pen.lineTo((0, height)); pen.closePath()
        glyph = pen.glyph()
        sha = TrueTypeGlyphInspector(_font(glyph, "A")).glyph_sha256(1)
        _GLYPHS[sha], _SEEDS[seed] = glyph, sha
    return _SEEDS[seed]


fixture_glyph_sha = register_fixture_glyph


def write_fixture_pdf(pdf, specs):
    """Write one isolated PDF from char/x0/y0/glyph_sha256 dictionaries."""
    pdf = Path(pdf); pdf.parent.mkdir(parents=True, exist_ok=True)
    width = max(300, max(float(s.get("x0", 10)) for s in specs) + 60)
    height = max(300, max(float(s.get("y0", 20)) for s in specs) + 60)
    with fitz.open() as document:
        page = document.new_page(width=width, height=height)
        fonts = {}
        for spec in specs:
            sha = spec["glyph_sha256"]
            if sha not in _GLYPHS:
                raise ValueError("fixture exact key must come from register_fixture_glyph")
            if sha not in fonts:
                name = "Fixture" + sha[:12]
                characters = {item.get("char", "轉") for item in specs if item["glyph_sha256"] == sha}
                fonts[sha] = (name, page.insert_font(fontname=name, fontbuffer=_font(_GLYPHS[sha], characters)))
            name, _xref = fonts[sha]
            page.insert_text((float(spec.get("x0", 10)), float(spec.get("y0", 20)) + 16),
                             spec.get("char", "轉"), fontname=name, fontsize=20)
        document.save(pdf)
    return pdf


def bind_source_from_pdf(entry, pdf, *, x0=None, y0=None):
    """Bind an entry to its unique real draw, retaining IDs and caller fields."""
    pdf = Path(pdf)
    x0 = float(entry.get("x0", 10) if x0 is None else x0)
    y0 = float(entry.get("y0", 20) if y0 is None else y0)
    with fitz.open(pdf) as document:
        page = document[0]
        found = [(span, char) for span in page.get_texttrace() for char in span["chars"]
                 if abs(char[3][0] - x0) < .001 and abs(char[3][1] - y0) < .001]
        if len(found) != 1:
            raise ValueError("fixture source draw is not unique")
        span, char = found[0]
        fonts = [font for font in page.get_fonts(full=True)
                 if normalize_basefont(font[3]) == normalize_basefont(span["font"])]
        if len(fonts) != 1:
            raise ValueError("fixture font resource is not unique")
        xref = fonts[0][0]
        sha = TrueTypeGlyphInspector(document.extract_font(xref)[3]).glyph_sha256(char[1])
        source = entry.setdefault("source_record", {})
        source.update({"font_xref": xref, "glyph_id_字形索引": char[1],
                       "注音元件ID": char[1], "TTF字形SHA256": sha})
        entry.update({"pdf": str(pdf), "pdf_name": pdf.name,
                      "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                      "physical_page": 1, "printed_page": entry.get("printed_page", "1"),
                      "char": chr(char[0]), "font_xref": xref, "glyph_id": char[1],
                      "zhuyin_component_id": char[1],
                      **dict(zip(("x0", "y0", "x1", "y1"), char[3]))})
    return entry


def source_entry(seed_or_digest, *, occurrence_id, review_id=None, char="轉", pdf_name="book.pdf",
                 x0=10.0, y0=20.0, **fields):
    """Return one real temporary source; repeated identical requests are stable."""
    global _TEMP
    sha = seed_or_digest if seed_or_digest in _GLYPHS else register_fixture_glyph(seed_or_digest)
    key = json.dumps([sha, char, pdf_name, float(x0), float(y0)], ensure_ascii=False)
    if key not in _CACHE:
        if _TEMP is None:
            _TEMP = tempfile.TemporaryDirectory(prefix="actual-source-")
        pdf = Path(_TEMP.name) / hashlib.sha256(key.encode()).hexdigest()[:12] / pdf_name
        write_fixture_pdf(pdf, [{"glyph_sha256": sha, "char": char, "x0": x0, "y0": y0}])
        _CACHE[key] = pdf
    entry = {"occurrence_id": occurrence_id, "review_id": review_id or "review-" + occurrence_id,
             "char": char, "x0": x0, "y0": y0, "source_record": {}, **fields}
    return bind_source_from_pdf(entry, _CACHE[key])
