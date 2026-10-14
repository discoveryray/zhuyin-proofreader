"""Genuine isolated font/PDF/painting proof; no window or success substitution."""
import copy
import hashlib
import io

import fitz
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from actual_source_visual_safety import source_visual_safety
from export_pdf_text_diagnostics import TrueTypeGlyphInspector


def make_visual_source(root, *, painting=None, count=2, rotation=0, outside_tone=False):
    """Reusable real PDF entries for source and guarded writer regressions."""
    root.mkdir(parents=True, exist_ok=True)
    builder = FontBuilder(1000, isTTF=True)
    names = [".notdef", "a", "b"]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: "a", 66: "b"})
    glyphs = {}
    for index, name in enumerate(names):
        pen = TTGlyphPen(None)
        if index:
            pen.moveTo((0, 0)); pen.lineTo((250, 0))
            pen.lineTo((250, 500)); pen.lineTo((0, 500)); pen.closePath()
            if outside_tone:
                # A separate tiny printed tone lies beyond the font's 600-unit
                # advance. A layout bbox cannot certify this complete ink.
                pen.moveTo((700, 400)); pen.lineTo((750, 400))
                pen.lineTo((750, 450)); pen.lineTo((700, 450)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "ActualVisualFixture", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    font = io.BytesIO(); builder.save(font)
    pdf = root / "book.pdf"
    with fitz.open() as document:
        page = document.new_page(width=220, height=max(240, 50 + count*40))
        xref = page.insert_font(fontname="ActualVisual", fontbuffer=font.getvalue())
        for index in range(count):
            page.insert_text((40, 50 + index*40), "A", fontname="ActualVisual", fontsize=20)
        box = fitz.Rect(page.get_texttrace()[0]["chars"][0][3])
        if painting == "path":
            page.draw_rect(box, fill=(1, 1, 1), color=None)
        elif painting == "replacement":
            page.draw_rect(box, fill=(1, 1, 1), color=None)
            page.insert_text((40, 50), "B", fontname="ActualVisual", fontsize=20)
        elif painting == "image":
            image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
            image.clear_with(255)
            page.insert_image(box, stream=image.tobytes("png"))
        elif painting == "unrelated":
            page.draw_rect(fitz.Rect(140, 140, 150, 150), fill=(1, 1, 1), color=None)
        elif painting == "same-slot":
            content = page.get_contents()[0]
            original = document.xref_stream(content)
            assert b"[<0001>]TJ" in original
            document.update_stream(content, original.replace(b"[<0001>]TJ", b"[<0001> 600 <0002>]TJ"))
        page.set_rotation(rotation)
        document.save(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    rows = []
    with fitz.open(pdf) as document:
        inspector = TrueTypeGlyphInspector(document.extract_font(xref)[3])
        for index, span in enumerate(document[0].get_texttrace()[:count]):
            codepoint, gid, _origin, bounds = span["chars"][0]
            source = {"font_xref": xref, "glyph_id_字形索引": gid, "注音元件ID": gid,
                      "TTF字形SHA256": inspector.glyph_sha256(gid)}
            row = {"occurrence_id": "occ_" + hashlib.sha256(str(index).encode()).hexdigest(),
                   "review_id": "rev_" + hashlib.sha256(("review"+str(index)).encode()).hexdigest(),
                   "pdf": str(pdf), "pdf_name": pdf.name, "pdf_sha256": digest,
                   "physical_page": 1, "printed_page": "1", "char": chr(codepoint),
                   "font_xref": xref, "glyph_id": gid, "zhuyin_component_id": gid,
                   "stable_key": "ActualVisual#" + str(gid), "source_record": source,
                   "actual": "", "state": "ACTUAL_DECODE_ERROR",
                   **dict(zip(("x0", "y0", "x1", "y1"), bounds))}
            rows.append(row)
    return rows


def clipped_visual_source(root, prefix, *, suffix=b"\nQ\n", outside_tone=False, page_group=None):
    """Handwritten PDF clipping operators; real bytes/seal/font/trace remain."""
    row = make_visual_source(root / "normal", count=1, outside_tone=outside_tone)[0]
    original = root / "normal/book.pdf"
    clipped = root / "clipped.pdf"
    with fitz.open(original) as document:
        if page_group is not None:
            fields = "/S /Transparency /I true /K false"
            if page_group == "rgb":
                fields += " /CS /DeviceRGB"
            else:
                assert page_group == "neutral"
            document.xref_set_key(document[0].xref, "Group", "<< " + fields + " >>")
        for xref in document[0].get_contents():
            content = document.xref_stream(xref)
            document.update_stream(xref, prefix + content + suffix)
        document.save(clipped)
    row["pdf"], row["pdf_name"] = str(clipped), clipped.name
    row["pdf_sha256"] = hashlib.sha256(clipped.read_bytes()).hexdigest()
    return row


def test_real_source_retains_exact_donor_and_sealed_snapshot(tmp_path):
    rows = make_visual_source(tmp_path / "source", painting="unrelated")
    for row in rows:
        proof = source_visual_safety(row)
        assert proof["location_readable"] and proof["donor_eligible"]
        with fitz.open(stream=(tmp_path / "source/book.pdf").read_bytes(), filetype="pdf") as document:
            assert source_visual_safety(row, document=document, page=document[0]) == proof
    rotated = make_visual_source(tmp_path / "rotated", count=6, rotation=90)
    assert rotated[-1]["y1"] > 220
    assert source_visual_safety(rotated[-1])["donor_eligible"]


def test_later_replacement_paths_and_images_are_unprovable(tmp_path):
    for painting in ("path", "replacement", "image"):
        first, peer = make_visual_source(tmp_path / painting, painting=painting)
        proof = source_visual_safety(first)
        assert not proof["location_readable"] and not proof["donor_eligible"]
        assert proof["reason"] in ("LATER_PAINT_SOURCE_UNPROVABLE", "SOURCE_SLOT_CONTRADICTION")
        assert source_visual_safety(peer)["donor_eligible"]
    first, peer = make_visual_source(tmp_path / "same-slot", painting="same-slot")
    assert source_visual_safety(first)["reason"] == "SOURCE_SLOT_CONTRADICTION"
    assert source_visual_safety(peer)["donor_eligible"]


def test_source_tuple_and_raw_exact_sha_are_reproved(tmp_path):
    row = make_visual_source(tmp_path / "source")[0]
    assert row["glyph_id"] == row["source_record"]["glyph_id_字形索引"] == 1
    for side, key in (("source_record", "glyph_id_字形索引"), (None, "glyph_id"),
                      ("source_record", "注音元件ID"), (None, "zhuyin_component_id")):
        changed = copy.deepcopy(row)
        target = changed[side] if side else changed
        target[key] = True
        proof = source_visual_safety(changed)
        assert not proof["location_readable"] and not proof["donor_eligible"]
        assert proof["reason"].startswith("SOURCE_NUMERIC_TYPE_INVALID:")
    blank = copy.deepcopy(row)
    blank["zhuyin_component_id"] = blank["source_record"]["注音元件ID"] = ""
    assert source_visual_safety(blank)["donor_eligible"]  # actual root bytes independently match
    del blank["source_record"]["TTF字形SHA256"]
    proof = source_visual_safety(blank)
    assert proof["location_readable"] and not proof["donor_eligible"]
    assert proof["reason"] == "SOURCE_LOCATION_ONLY_NO_EXACT_PROOF"
    blank["source_record"]["注音元件ID"] = "1"
    assert source_visual_safety(blank)["reason"].startswith("SOURCE_NUMERIC_TYPE_INVALID:")
    for key, value in (("glyph_id_字形索引", 999), ("font_xref", 999),
                       ("字元", "B"), ("x0", row["x0"] + 5),
                       ("occurrence_id", "other"), ("pdf_sha256", "0"*64)):
        changed = copy.deepcopy(row); changed["source_record"][key] = value
        assert not source_visual_safety(changed)["location_readable"]
    changed = copy.deepcopy(row); changed["source_record"]["TTF字形SHA256"] = "0"*64
    assert source_visual_safety(changed)["reason"] == "SOURCE_EXACT_SHA_MISMATCH"
    changed = copy.deepcopy(row); changed["source_record"]["注音元件ID"] = 2
    changed["zhuyin_component_id"] = 2
    assert source_visual_safety(changed)["reason"] == "SOURCE_COMPONENT_NOT_ATTACHED"


def test_location_without_exact_proof_is_never_a_donor(tmp_path):
    row = make_visual_source(tmp_path / "source")[0]
    del row["source_record"]["TTF字形SHA256"]
    proof = source_visual_safety(row)
    assert proof["location_readable"] and not proof["donor_eligible"]
    assert proof["reason"] == "SOURCE_LOCATION_ONLY_NO_EXACT_PROOF"


def test_stale_dirty_or_foreign_snapshot_cannot_authorize(tmp_path):
    row = make_visual_source(tmp_path / "source")[0]
    pdf = tmp_path / "source/book.pdf"
    raw = pdf.read_bytes()
    with fitz.open(pdf) as unsealed:
        assert source_visual_safety(row, document=unsealed)["reason"] == "SOURCE_DOCUMENT_BYTES_UNAVAILABLE"
    with fitz.open(stream=raw, filetype="pdf") as document:
        with fitz.open(stream=raw, filetype="pdf") as foreign:
            assert source_visual_safety(row, document=document, page=foreign[0])["reason"] == "SOURCE_PAGE_OWNERSHIP_MISMATCH"
        document[0].draw_rect(fitz.Rect(30, 30, 60, 60), fill=(1, 1, 1))
        assert source_visual_safety(row, document=document)["reason"] == "SOURCE_DOCUMENT_UNSEALED"
    pdf.write_bytes(raw + b"\n% changed bytes\n")
    assert source_visual_safety(row)["reason"] == "SOURCE_PDF_SHA_MISMATCH"
    with fitz.open(stream=raw, filetype="pdf") as document:
        assert source_visual_safety(row, pdf=pdf, document=document)["reason"] == "SOURCE_PDF_SHA_MISMATCH"


def test_missing_ambiguous_or_moved_source_never_confirms_neighbor(tmp_path):
    row = make_visual_source(tmp_path / "source")[0]
    for key in ("pdf", "pdf_sha256", "char", "occurrence_id", "review_id"):
        changed = copy.deepcopy(row); changed.pop(key)
        assert not source_visual_safety(changed)["location_readable"]
    changed = copy.deepcopy(row); changed["x0"] += 20; changed["x1"] += 20
    assert source_visual_safety(changed)["reason"] == "SOURCE_DRAW_NOT_UNIQUE"
    with fitz.open(row["pdf"]) as document:
        page = document[0]
        page.insert_text((40, 50), "A", fontname="ActualVisual", fontsize=20)
        duplicated = tmp_path / "duplicated.pdf"; document.save(duplicated)
    changed = copy.deepcopy(row); changed["pdf"] = str(duplicated)
    changed["pdf_sha256"] = hashlib.sha256(duplicated.read_bytes()).hexdigest()
    assert source_visual_safety(changed)["reason"] == "SOURCE_DRAW_NOT_UNIQUE"


def test_active_clip_cannot_certify_blank_or_partial_annotation(tmp_path):
    for name, bounds, operator in (("w", b"0 0 1 1", b"W"),
            ("wstar", b"0 0 1 1", b"W*"), ("empty", b"0 0 0 0", b"W")):
        row = clipped_visual_source(tmp_path / name,
            b"q\n" + bounds + b" re " + operator + b" n\n")
        with fitz.open(row["pdf"]) as document:
            # Independent whole-page oracle: no glyph ink at all remains.
            assert all(value == 255 for value in document[0].get_pixmap(alpha=False).samples)
        proof = source_visual_safety(row)
        assert not proof["location_readable"] and not proof["donor_eligible"]
        assert "CLIP" in proof["reason"]
    partial = clipped_visual_source(tmp_path / "partial-tone",
        b"q\n0 0 53 240 re W n\n", outside_tone=True)
    assert partial["x1"] < 53  # the full layout bbox fits; printed tone does not
    with fitz.open(tmp_path / "partial-tone/normal/book.pdf") as document:
        original_tone = document[0].get_pixmap(matrix=fitz.Matrix(4, 4),
            clip=fitz.Rect(54, 41, 55, 42), alpha=False)
        assert min(original_tone.samples) < 255
    with fitz.open(partial["pdf"]) as document:
        pix = document[0].get_pixmap(alpha=False)
        assert min(pix.samples) < 255  # left base remains, never a success proof
        tone = document[0].get_pixmap(matrix=fitz.Matrix(4, 4),
            clip=fitz.Rect(54, 41, 55, 42), alpha=False)
        assert all(value == 255 for value in tone.samples)
    assert not source_visual_safety(partial)["location_readable"]


def test_clip_scope_restore_and_complete_rectangles_preserve_normal_source(tmp_path):
    for name, prefix, suffix in (
            ("complete", b"q\n0 0 220 240 re W n\n", b"\nQ\n"),
            ("complete-even-odd", b"q\n0 0 220 240 re W* n\n", b"\nQ\n"),
            ("restored", b"q\n0 0 1 1 re W n\nQ\n", b""),
            ("nested-restored", b"q\n0 0 220 240 re W n\nq\n0 0 1 1 re W* n\nQ\n", b"\nQ\n")):
        row = clipped_visual_source(tmp_path / name, prefix, suffix=suffix)
        assert source_visual_safety(row)["donor_eligible"]
    # An even-odd hole and a non-rectangular active clip cannot be certified
    # from their outer bounding rectangles, even if those contain layout bbox.
    for name, prefix in (("hole", b"q\n0 0 220 240 re 39 185 14 24 re W* n\n"),
                         ("triangle", b"q\n30 180 m 60 180 l 30 220 l h W n\n")):
        row = clipped_visual_source(tmp_path / name, prefix)
        assert not source_visual_safety(row)["donor_eligible"]
    neutral = clipped_visual_source(tmp_path / "neutral-group", b"", suffix=b"", page_group="neutral")
    assert source_visual_safety(neutral)["donor_eligible"]
    unknown = clipped_visual_source(tmp_path / "unknown-group", b"", suffix=b"", page_group="rgb")
    proof = source_visual_safety(unknown)
    assert not proof["location_readable"] and "GROUP" in proof["reason"]
    text_clip = b"q\nBT /ActualVisual 20 Tf 7 Tr 1 0 0 1 100 190 Tm <0001> Tj ET\n"
    active = clipped_visual_source(tmp_path / "text-clip", text_clip + b"BT 0 Tr ET\n")
    assert not source_visual_safety(active)["location_readable"]
    restored = clipped_visual_source(tmp_path / "text-clip-restored", text_clip + b"Q\n", suffix=b"")
    assert source_visual_safety(restored)["donor_eligible"]
