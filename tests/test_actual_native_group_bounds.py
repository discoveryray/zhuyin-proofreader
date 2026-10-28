"""Finite native CMYK/closed-boundary proof, real isolated PDF/font sources."""
import copy
import hashlib
from pathlib import Path

import fitz

import actual_source_visual_safety as safety
from tests.test_actual_source_visual_safety import make_visual_source


def _variant(row, destination, *, prefix=b"", suffix=b"", group=None, shade=None, form=False):
    with fitz.open(row["pdf"]) as document:
        page = document[0]
        if group is not None and not form:
            document.xref_set_key(page.xref, "Group", group)
        if shade is not None:
            function = document.get_new_xref()
            document.update_object(function, "<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [0 0 0 0] /N 1 >>")
            shading = document.get_new_xref()
            document.update_object(shading, f"<< /ShadingType 2 /ColorSpace /DeviceCMYK /Coords [0 0 220 0] /Extend [true true] /Function {function} 0 R >>")
            resources = int(document.xref_get_key(page.xref, "Resources")[1].split()[0])
            document.xref_set_key(resources, "Shading", f"<< /C13Shade {shading} 0 R >>")
            paint = b"q\n/C13Shade sh\nQ\n"
            if shade == "early":
                prefix = paint + prefix
            elif shade == "late-transparent":
                state = document.get_new_xref()
                document.update_object(state, "<< /Type /ExtGState /ca 0 /CA 0 >>")
                document.xref_set_key(resources, "ExtGState", f"<< /C13Zero {state} 0 R >>")
                suffix += b"q\n/C13Zero gs\n/C13Shade sh\nQ\n"
            else:
                raise AssertionError(shade)
        for xref in page.get_contents():
            document.update_stream(xref, prefix + document.xref_stream(xref) + suffix)
        if form:
            resources = document.xref_get_key(page.xref, "Resources")[1]
            form_xref = document.get_new_xref()
            document.update_object(form_xref, f"<< /Type /XObject /Subtype /Form /BBox [0 0 220 240] /Resources {resources} /Group {group} >>")
            document.update_stream(form_xref, b"\n".join(document.xref_stream(xref) for xref in page.get_contents()))
            # Separate the page resources from the Form's retained resources;
            # never create a recursive XObject reference in this real PDF.
            page_resources = document.get_new_xref()
            document.update_object(page_resources, f"<< /XObject << /C13Form {form_xref} 0 R >> >>")
            document.xref_set_key(page.xref, "Resources", f"{page_resources} 0 R")
            content = document.get_new_xref(); document.update_object(content, "<< >>")
            document.update_stream(content, b"q\n/C13Form Do\nQ\n"); page.set_contents(content)
        document.save(destination)
    return {**copy.deepcopy(row), "pdf": str(destination), "pdf_name": destination.name,
            "pdf_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


CMYK = "<< /S /Transparency /I true /K false /CS /DeviceCMYK >>"
# Actual PDF coordinates: source ink is x40..45, y190..200. Rounded corners
# and their full cubic control hulls stay far from the complete source ink.
ROUND = (b"25 10 m\n180 10 l\n195 10 210 25 210 40 c\n210 215 l\n"
         b"210 225 200 235 190 235 c\n25 235 l\n15 235 5 225 5 215 c\n"
         b"5 30 l\n5 20 15 10 25 10 c\nh\n")
FAR_CORNERS = (b"15 10 m\n200 10 l\n205 10 210 15 210 20 c\n210 225 l\n"
               b"210 230 205 235 200 235 c\n15 235 l\n10 235 5 230 5 225 c\n"
               b"5 20 l\n5 15 10 10 15 10 c\nh\n")


def _pixels(row, box=fitz.Rect(38, 38, 48, 52)):
    with fitz.open(row["pdf"]) as document:
        return document[0].get_pixmap(matrix=fitz.Matrix(4, 4), clip=box, alpha=False).samples


def _coverage(pixels):
    return bytes(min(pixels[index:index+3]) < 255 for index in range(0, len(pixels), 3))


def test_cmyk_neutral_group_proves_source_and_nonopaque_group_stays_closed(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    normal = _variant(row, tmp_path / "cmyk.pdf", group=CMYK)
    # Native CMYK conversion legitimately changes RGB channel values; the
    # independently rendered source geometry/coverage must remain identical.
    assert _coverage(_pixels(normal)) == _coverage(_pixels(row))
    assert 0 < sum(_coverage(_pixels(normal))) < len(_coverage(_pixels(normal)))
    assert safety.source_visual_safety(normal)["donor_eligible"]
    nonisolated = _variant(row, tmp_path / "nonisolated.pdf",
                          group=CMYK.replace("/I true", "/I false"), form=True)
    proof = safety.source_visual_safety(nonisolated)
    assert not proof["location_readable"] and proof["reason"] == "SOURCE_ACTIVE_GROUP_UNPROVABLE"
    # The existing RGB-group negative remains a separate unchanged regression.
    rgb = _variant(row, tmp_path / "rgb.pdf", group=CMYK.replace("DeviceCMYK", "DeviceRGB"))
    assert safety.source_visual_safety(rgb)["reason"] == "SOURCE_ACTIVE_GROUP_UNPROVABLE"


def test_cmyk_same_colour_and_later_cover_never_certify_source(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    white = _variant(row, tmp_path / "same-colour.pdf", group=CMYK, prefix=b"1 g\n")
    assert all(value == 255 for value in _pixels(white))
    assert not safety.source_visual_safety(white)["location_readable"]
    covered = _variant(row, tmp_path / "covered.pdf", group=CMYK,
                       suffix=b"q\n1 g\n38 188 12 15 re f\nQ\n")
    assert all(value == 255 for value in _pixels(covered))
    proof = safety.source_visual_safety(covered)
    assert not proof["location_readable"] and proof["overlay_status"] == "CONFIRMED"


def test_closed_cubic_clip_and_later_stroke_frame_preserve_complete_ink(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    uncertain = _variant(row, tmp_path / "close-corner.pdf", group=CMYK,
        prefix=b"q\n" + ROUND + b"W n\n", suffix=b"Q\nq\n2 w\n" + ROUND + b"S\nQ\n")
    control = _variant(row, tmp_path / "control.pdf", group=CMYK)
    assert _pixels(uncertain) == _pixels(control)
    # Native stroke/miter expansion of this corner touches the ink. Matching
    # finite pixels cannot silently convert its continuous uncertainty to PASS.
    proof = safety.source_visual_safety(uncertain)
    assert not proof["location_readable"] and proof["reason"] == "SOURCE_LATER_INK_INTERSECTION_UNPROVABLE"
    frame = _variant(row, tmp_path / "frame.pdf", group=CMYK,
        prefix=b"q\n" + FAR_CORNERS + b"W n\n", suffix=b"Q\nq\n2 w\n" + FAR_CORNERS + b"S\nQ\n")
    assert _pixels(frame) == _pixels(control)
    assert safety.source_visual_safety(frame)["donor_eligible"]
    crossing = _variant(row, tmp_path / "crossing.pdf", group=CMYK,
        prefix=b"q\n" + ROUND + b"W n\n", suffix=b"Q\nq\n1 G\n8 w\n42 185 m\n42 205 l\nS\nQ\n")
    assert _pixels(crossing) != _pixels(control)
    assert not safety.source_visual_safety(crossing)["location_readable"]


def test_cubic_clip_hole_and_corner_do_not_pass_from_outer_bbox(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    hole = _variant(row, tmp_path / "hole.pdf", group=CMYK,
        prefix=b"q\n" + ROUND + b"38 188 12 15 re\nW* n\n", suffix=b"Q\n")
    assert all(value == 255 for value in _pixels(hole))
    assert not safety.source_visual_safety(hole)["location_readable"]
    # Outer bbox contains the whole source metrics, but the cubic boundary
    # cuts its actual ink. Its control hull may never be treated as fill.
    corner = _variant(row, tmp_path / "corner.pdf", group=CMYK,
        prefix=b"q\n30 180 m\n100 180 l\n100 210 l\n60 210 l\n60 192 42 192 30 180 c\nh\nW n\n",
        suffix=b"Q\n")
    control = _variant(row, tmp_path / "control.pdf", group=CMYK)
    assert _coverage(_pixels(corner)) != _coverage(_pixels(control))
    assert not safety.source_visual_safety(corner)["location_readable"]


def test_native_boundaries_keep_winding_touch_open_and_capture_limit_closed():
    native = safety._native_pdf.mupdf
    ctm = native.FzMatrix().internal()
    path = native.FzPath()
    for points in (((0, 0), (100, 0), (100, 100), (0, 100)),
                   ((20, 20), (80, 20), (80, 80), (20, 80))):
        path.fz_moveto(*points[0])
        for point in points[1:]: path.fz_lineto(*point)
        path.fz_closepath()
    boundaries = safety._native_boundaries(path.m_internal, ctm)
    assert safety._boundary_contains(boundaries, 0, fitz.Rect(40, 40, 50, 50))
    assert not safety._boundary_contains(boundaries, 1, fitz.Rect(40, 40, 50, 50))
    assert not safety._boundary_contains(boundaries, 0, fitz.Rect(20, 40, 30, 50))
    opposite = native.FzPath()
    for points in (((0, 0), (100, 0), (100, 100), (0, 100)),
                   ((20, 20), (20, 80), (80, 80), (80, 20))):
        opposite.fz_moveto(*points[0])
        for point in points[1:]: opposite.fz_lineto(*point)
        opposite.fz_closepath()
    assert not safety._boundary_contains(safety._native_boundaries(opposite.m_internal, ctm),
                                         0, fitz.Rect(40, 40, 50, 50))
    opened = native.FzPath(); opened.fz_moveto(0, 0); opened.fz_curveto(0, 100, 100, 100, 100, 0)
    assert safety._native_boundaries(opened.m_internal, ctm) is None
    excessive = native.FzPath(); excessive.fz_moveto(0, 0)
    for index in range(2100): excessive.fz_lineto(index, index % 3)
    excessive.fz_closepath()
    assert safety._native_boundaries(excessive.m_internal, ctm) is None
    # The collector refuses before accumulating the overflow/partial path.
    walker = safety._NativeContours(record_commands=True)
    native.fz_walk_path(excessive, walker, walker.m_internal)
    assert walker.failure == "SOURCE_NATIVE_BOUNDARY_CAPTURE_LIMIT"
    assert len(walker.commands) == 2048 and walker.current is None


def test_infinite_shade_before_source_keeps_sequence_and_later_unknown_refuses(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    early = _variant(row, tmp_path / "early.pdf", group=CMYK, shade="early")
    control = _variant(row, tmp_path / "control.pdf", group=CMYK)
    assert _pixels(early) == _pixels(control)
    assert safety.source_visual_safety(early)["donor_eligible"]
    later = _variant(row, tmp_path / "late.pdf", group=CMYK, shade="late-transparent")
    assert _pixels(later) == _pixels(control)
    proof = safety.source_visual_safety(later)
    assert not proof["location_readable"] and proof["overlay_status"] == "SUSPECTED"
    with safety.source_visual_operation() as operation:
        raw = operation.read_source(later["pdf"], later["pdf_sha256"])
        analysis = operation._page(raw, later["pdf_sha256"], 1)
        unknown = [paint for paint in analysis["footprints"] if paint["bounds"] is None]
        assert len(unknown) == 1 and not unknown[0]["empty"]
        assert analysis["paint_calls"] == ((0, "fill_text"), (1, "fill_shade"))


def test_complete_operation_outline_union_excludes_metrics_padding_but_not_other_glyphs(tmp_path):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    control = _variant(row, tmp_path / "control.pdf", group=CMYK)
    prefix = b"q\n39.5 189.5 6 11 re W n\n"
    clipped = _variant(row, tmp_path / "padding-only.pdf", group=CMYK,
                       prefix=prefix, suffix=b"Q\n")
    # Independently built glyph ink is x40..45, y40..50. Native text bounds
    # add padding; the clip excludes that padding without changing any ink.
    assert _pixels(clipped) == _pixels(control)
    with safety.source_visual_operation() as operation:
        proof = safety.source_visual_safety(clipped)
        assert proof["donor_eligible"], proof
        raw = operation.read_source(clipped["pdf"], clipped["pdf_sha256"])
        analysis = operation._page(raw, clipped["pdf_sha256"], 1)
        assert analysis["draws"][0]["ink"] == (40.0, 40.0, 45.0, 50.0)
    # The second glyph is in the SAME native text operation. Proving the
    # selected first glyph alone cannot hide the other's clipped true ink.
    second_pdf = tmp_path / "second-clipped.pdf"
    with fitz.open(control["pdf"]) as document:
        xref = document[0].get_contents()[0]
        stream = document.xref_stream(xref)
        assert b"[<0001>]TJ" in stream
        document.update_stream(xref, prefix + stream.replace(b"[<0001>]TJ", b"[<00010001>]TJ") + b"Q\n")
        document.save(second_pdf)
    second = {**copy.deepcopy(row), "pdf": str(second_pdf), "pdf_name": second_pdf.name,
              "pdf_sha256": hashlib.sha256(second_pdf.read_bytes()).hexdigest()}
    assert _pixels(second) == _pixels(control)
    assert not safety.source_visual_safety(second)["location_readable"]
