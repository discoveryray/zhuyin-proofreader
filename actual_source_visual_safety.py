"""Actual-only, current-source checks before visual confirmation or reuse.

This is a bounded final-paint refusal check, not a pronunciation decoder or a
general PDF visibility interpreter. Unknown intersections cannot prove that a
target is visible. Results are transient and must be recomputed at write gates.
"""
from __future__ import annotations

import hashlib
import io
import base64
import json
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import get_ident

import fitz
import pymupdf as _native_pdf
from fontTools.ttLib import TTFont

from export_pdf_text_diagnostics import (
    TrueTypeGlyphInspector, build_font_lookup, normalize_basefont, resolve_font,
)
from cff_zhuyin_decoder import CFFZhuyinInspector, cff_glyph_outline_sha256

_BOX_KEYS = ("x0", "y0", "x1", "y1")
_CURRENT_OPERATION = ContextVar("actual_source_visual_operation", default=None)


class SourceVisualCleanupError(RuntimeError):
    """Owned document teardown failed; never a source qualification result."""


class SourceVisualPostCommitCleanupError(RuntimeError):
    """A completed durable writer retains its result for existing recovery."""
    def __init__(self, result, commit_state, cause):
        self.completed_result, self.commit_state, self.cause = result, commit_state, cause
        super().__init__(f"{commit_state}: source cleanup failed after durable completion: {cause}")


def source_visual_write_result(action, *, commit_state, committed=lambda result: True, completed_cleanup=None):
    """Only completed product writes may carry a result across teardown failure.

    This does not catch proof/device/body errors or change source eligibility.
    The caller's durable result predicate classifies cleanup only after action
    returns normally; it never authorizes a write or a checked source.
    """
    completed = False
    try:
        with source_visual_operation():
            result = action()
            completed = True
        return result
    except SourceVisualCleanupError as exc:
        if not completed or not committed(result):
            raise
        if completed_cleanup is not None:
            return completed_cleanup(result, exc)
        raise SourceVisualPostCommitCleanupError(result, commit_state, exc) from exc


def _file_revision(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class SourceVisualOperation:
    """Owned immutable sources and native analysis for one operation/thread."""
    def __init__(self):
        self._owner = get_ident()
        self._closed = False
        self._sources, self._documents, self._pages, self._raws = {}, {}, {}, {}
        self._tokens = []

    def _require_owner(self):
        if self._closed or get_ident() != self._owner:
            raise ValueError("SOURCE_VISUAL_OPERATION_CLOSED_OR_WRONG_OWNER")

    def read_source(self, pdf_path, expected_sha):
        self._require_owner()
        path = Path(pdf_path).resolve(strict=True)
        revision = _file_revision(path)
        previous = self._sources.get(path)
        if previous is not None:
            raw, digest, initial_revision = previous
            if digest != expected_sha or revision != initial_revision:
                raise ValueError("SOURCE_FILE_CHANGED_DURING_OPERATION")
            return raw
        raw = path.read_bytes()
        if _file_revision(path) != revision:
            raise ValueError("SOURCE_FILE_CHANGED_DURING_READ")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha:
            raise ValueError("SOURCE_PDF_SHA_MISMATCH")
        self._sources[path] = (raw, digest, revision)
        self._raws[digest] = raw
        return raw

    def _seal_bytes(self, raw, expected_sha):
        self._require_owner()
        previous = self._raws.get(expected_sha)
        if previous is not None:
            if previous != raw:
                raise ValueError("SOURCE_PDF_SHA_MISMATCH")
            return previous
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("SOURCE_PDF_SHA_MISMATCH")
        self._raws[expected_sha] = raw
        return raw

    def recheck_sources(self):
        self._require_owner()
        for path, (raw, digest, revision) in self._sources.items():
            if (_file_revision(path) != revision or path.read_bytes() != raw
                    or hashlib.sha256(raw).hexdigest() != digest):
                raise ValueError("SOURCE_FILE_CHANGED_DURING_OPERATION")
        return True

    def _page(self, raw, digest, physical_page):
        self._require_owner()
        previous = self._documents.get(digest)
        if previous is None:
            previous = (raw, fitz.open(stream=raw, filetype="pdf"))
            self._documents[digest] = previous
        if previous[0] != raw:
            raise ValueError("SOURCE_SNAPSHOT_BYTES_CONTRADICTION")
        key = digest, physical_page
        if key not in self._pages:
            page = previous[1][physical_page - 1]
            if page.rotation:
                page.set_rotation(0)  # only the operation's private snapshot
            traces = page.get_texttrace()
            try:
                captured = _capture_native_page(page, traces)
            except Exception as exc:
                # The same immutable page cannot gain a valid source proof by
                # repeatedly retrying an unsupported native capture.
                self._pages[key] = ValueError(str(exc) or type(exc).__name__)
                raise
            captured.update(page=page, document=previous[1], traces=traces,
                            lookup=build_font_lookup(page.get_fonts(full=True)))
            self._pages[key] = captured
        if isinstance(self._pages[key], Exception):
            raise self._pages[key]
        return self._pages[key]

    def close(self):
        if get_ident() != self._owner:
            raise ValueError("SOURCE_VISUAL_OPERATION_WRONG_OWNER")
        if self._closed:
            return
        failures = []
        try:
            for _raw, document in list(self._documents.values()):
                try:
                    document.close()
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            self._pages.clear()
            self._documents.clear()
            self._sources.clear()
            self._raws.clear()
            self._closed = True
        if failures:
            raise SourceVisualCleanupError("; ".join(failures))

    def __enter__(self):
        self._require_owner()
        current = _CURRENT_OPERATION.get()
        if current is not None and current is not self:
            raise ValueError("SOURCE_VISUAL_OPERATION_OWNER_CONTRADICTION")
        self._tokens.append(_CURRENT_OPERATION.set(self))
        return self

    def __exit__(self, *args):
        _CURRENT_OPERATION.reset(self._tokens.pop())
        if not self._tokens:
            try:
                self.close()
            except SourceVisualCleanupError as cleanup:
                primary = args[1] if len(args) > 1 else None
                if primary is None:
                    raise
                primary.add_note("Secondary source teardown failure: " + str(cleanup))


@contextmanager
def source_visual_operation():
    current = _CURRENT_OPERATION.get()
    if current is not None:
        current._require_owner()
        yield current
    else:
        with SourceVisualOperation() as operation:
            yield operation


def recheck_current_sources():
    current = _CURRENT_OPERATION.get()
    if current is not None:
        return current.recheck_sources()
    return None


def _native_rect(value, *, allow_empty=False):
    bounds = (value.x0, value.y0, value.x1, value.y1)
    if not all(type(item) in (int, float) and math.isfinite(item) for item in bounds):
        raise ValueError("SOURCE_NATIVE_BOUNDS_INVALID")
    result = fitz.Rect(bounds)
    if result.is_empty and allow_empty:
        return None
    if result.is_empty or result.is_infinite:
        raise ValueError("SOURCE_NATIVE_BOUNDS_EMPTY")
    return tuple(result)


def _captured_native_callback(callback):
    """SWIG/native processing may catch callback exceptions: retain refusal."""
    def captured(self, *args):
        try:
            return callback(self, *args)
        except BaseException as exc:
            self.capture_failure = type(exc).__name__ + ": " + str(exc)
            self.active_clips.append({"kind": "capture-failed"})
            # A native tile callback requires an integer return even on error.
            return 0 if callback.__name__ == "begin_tile" else None
    return captured


def _record_native_paint(name, callback):
    @_captured_native_callback
    def recorded(self, *args):
        self.paint_calls.append((self.seqno, name))
        return callback(self, *args)
    return recorded


class _NativeContours(_native_pdf.mupdf.FzPathWalker2):
    """Split the native outline only; never interpret PDF content operators."""
    def __init__(self, *, record_commands=False):
        super().__init__()
        self.paths, self.current, self.failure = [], None, None
        self.commands = [] if record_commands else None
        # Optional compressed path operators are expanded by the native walker
        # to these four required operations, as in the vendor's own Walker.
        for name in ("moveto", "lineto", "curveto", "closepath"):
            getattr(self, "use_virtual_" + name)()

    def _call(self, name, *args):
        try:
            if self.commands is not None:
                if self.failure is not None:
                    return
                if len(self.commands) >= 2048 or len(self.paths) >= 256:
                    self.failure = "SOURCE_NATIVE_BOUNDARY_CAPTURE_LIMIT"
                    self.current = None
                    return
                self.commands.append((name, args))
            if name == "moveto":
                self.finish()
                self.current = _native_pdf.mupdf.FzPath()
            if self.current is None:
                raise ValueError("SOURCE_NATIVE_CONTOUR_ORDER_UNPROVABLE")
            getattr(self.current, "fz_" + name)(*args)
            if name == "closepath":
                self.finish()
        except BaseException as exc:
            self.failure = str(exc) or type(exc).__name__

    def finish(self):
        if self.current is not None:
            self.paths.append(self.current)
            self.current = None

    def moveto(self, ctx, x, y): self._call("moveto", x, y)
    def lineto(self, ctx, x, y): self._call("lineto", x, y)
    def curveto(self, ctx, *points): self._call("curveto", *points)
    def closepath(self, ctx): self._call("closepath")


def _native_contours(outline, stroke, ctm):
    native = _native_pdf.mupdf
    walker = _NativeContours()
    native.fz_walk_path(outline, walker, walker.m_internal)
    walker.finish()
    if walker.failure is not None:
        raise ValueError("SOURCE_NATIVE_CONTOUR_CAPTURE_UNPROVABLE:" + walker.failure)
    return [(path, bounds) for path in walker.paths
            if (bounds := _native_rect(native.ll_fz_bound_path(
                path.m_internal, stroke, ctm), allow_empty=True)) is not None]


def _native_boundaries(path, ctm, stroke=None):
    """Bound closed native line/cubic boundaries, never parse PDF operators.

    A cubic stays in its control-point convex hull. A chord is homotopic to
    that curve without crossing any rectangle disjoint from the entire hull.
    The native stroke expansion includes caps/joins/miter and the real CTM.
    Unknown/open/excessive geometry remains unsupported, rather than empty.
    """
    native = _native_pdf.mupdf
    walker = _NativeContours(record_commands=True)
    kept = native.FzPath(native.ll_fz_keep_path(path))
    native.fz_walk_path(kept, walker, walker.m_internal)
    walker.finish()
    if walker.failure is not None or not walker.commands or len(walker.commands) > 2048:
        return None
    current = start = None
    boundaries, subpaths = [], 0
    for name, points in walker.commands:
        if name == "moveto":
            if current is not None:
                return None
            current = start = tuple(points)
            subpaths += 1
            if subpaths > 256:
                return None
            continue
        if current is None or name not in {"lineto", "curveto", "closepath"}:
            return None
        controls = ([current, start] if name == "closepath" else
                    [current, *zip(points[::2], points[1::2])])
        if not all(type(value) in (int, float) and math.isfinite(value)
                   for point in controls for value in point):
            return None
        transformed = [native.fz_transform_point(native.fz_make_point(*point), native.FzMatrix(ctm))
                       for point in controls]
        box = (min(p.x for p in transformed), min(p.y for p in transformed),
               max(p.x for p in transformed), max(p.y for p in transformed))
        if not all(math.isfinite(value) for value in box):
            return None
        if stroke is not None:
            # internal() borrows the wrapper's storage; retain it through the call.
            box_owner = native.FzRect(*box)
            bound = native.ll_fz_adjust_rect_for_stroke(box_owner.internal(), stroke, ctm)
            box = _native_rect(bound)
        boundaries.append({"start": (transformed[0].x, transformed[0].y),
                           "end": (transformed[-1].x, transformed[-1].y), "bounds": box})
        current = None if name == "closepath" else controls[-1]
    return tuple(boundaries) if current is None and boundaries else None


def _boundary_disjoint(bounds, rectangle):
    # Strict separation: touching or numeric uncertainty never proves safety.
    return (bounds[2] < rectangle.x0 or bounds[0] > rectangle.x1
            or bounds[3] < rectangle.y0 or bounds[1] > rectangle.y1)


def _boundary_contains(boundaries, even_odd, rectangle):
    if type(even_odd) is not int or even_odd not in (0, 1) or not boundaries:
        return False
    if any(not _boundary_disjoint(part["bounds"], rectangle) for part in boundaries):
        return False
    # Since each full curve hull misses the rectangle, replacing it by its
    # chord preserves winding for every point of the connected rectangle.
    # A crossing chord's hull is strictly to one side: no division/epsilon.
    y, winding = (rectangle.y0 + rectangle.y1) / 2, 0
    for part in boundaries:
        a, b = part["start"], part["end"]
        if part["bounds"][0] <= rectangle.x1:
            continue
        if a[1] <= y < b[1]:
            winding += 1
        elif b[1] <= y < a[1]:
            winding -= 1
    return bool(winding % 2) if even_odd else winding != 0


def _native_group_colorspace(colorspace):
    if colorspace is None:
        return "inherited-null"
    native = _native_pdf.mupdf
    fields = tuple(getattr(native, "ll_fz_colorspace_" + name)(colorspace)
                   for name in ("is_device", "is_device_cmyk", "type", "n"))
    builtin_type = native.ll_fz_colorspace_type(native.fz_device_cmyk().m_internal)
    # Explicit native builtin CMYK only. Names/ICC/DeviceN/RGB do not qualify.
    if (all(type(value) is int for value in (*fields, builtin_type))
            and fields == (1, 1, builtin_type, 4)):
        return "builtin-device-cmyk"
    return "unsupported"


class _CurrentClipTrace(_native_pdf.JM_new_texttrace_device):
    """Native ordered clip state at text paint, not a PDF content interpreter.

    The installed vendor texttrace device already counts fill/stroke paths,
    fill/stroke/ignored text, shades, images and image masks. Clip transitions
    do not increment that paint sequence. No native pointer escapes a callback.
    """
    def __init__(self):
        super().__init__([])
        self.active_clips, self.groups, self.mask_definitions, self.draws = [], [], [], []
        self.paint_calls = []
        self.footprints = []
        self._outline_fonts = {}
        self.capture_failure = None
        for name in ("clip_path", "clip_stroke_path", "clip_text", "clip_stroke_text",
                     "clip_image_mask", "pop_clip", "begin_group", "end_group",
                     "begin_mask", "end_mask", "begin_tile", "end_tile"):
            getattr(self, "use_virtual_" + name)()

    # Inherited vendor callbacks participate in the same failure contract.
    ignore_text = _record_native_paint("ignore_text", _native_pdf.JM_new_texttrace_device.ignore_text)
    begin_layer = _captured_native_callback(_native_pdf.JM_new_texttrace_device.begin_layer)
    end_layer = _captured_native_callback(_native_pdf.JM_new_texttrace_device.end_layer)

    def _path_paint(self, name, path, stroke, ctm, args):
        native = _native_pdf.mupdf
        bounds = _native_rect(native.ll_fz_bound_path(path, stroke, ctm), allow_empty=True)
        inner = None
        if stroke is not None and bounds is not None:
            kept = native.FzPath(native.ll_fz_keep_path(path))
            rectangle = kept.fz_path_is_rect(native.FzMatrix(ctm))
            if type(rectangle) is not int or rectangle not in (0, 1):
                raise ValueError("SOURCE_NATIVE_PATH_RECT_PREDICATE_INVALID")
            if rectangle:
                center = _native_rect(native.ll_fz_bound_path(path, None, ctm), allow_empty=True)
                if center is not None:
                    # Native bound_path already includes the actual stroke,
                    # transform and joins. Its maximum outward expansion also
                    # bounds inward expansion of this axis-aligned rectangle.
                    margin = max(center[0]-bounds[0], center[1]-bounds[1],
                                 bounds[2]-center[2], bounds[3]-center[3])
                    safe = fitz.Rect(center[0]+margin, center[1]+margin,
                                     center[2]-margin, center[3]-margin)
                    if margin >= 0 and not safe.is_empty:
                        inner = tuple(safe)
        boundaries = _native_boundaries(path, ctm, stroke) if stroke is not None and bounds is not None else None
        self.footprints.append({"seqno": self.seqno, "bounds": bounds,
                                "empty": bounds is None, "inner": inner,
                                "stroke_boundaries": boundaries})
        self.paint_calls.append((self.seqno, name))
        getattr(_native_pdf.JM_new_texttrace_device, name)(self, *args)

    @_captured_native_callback
    def fill_path(self, ctx, path, even_odd, ctm, cs, color, alpha, params):
        self._path_paint("fill_path", path, None, ctm,
                         (ctx, path, even_odd, ctm, cs, color, alpha, params))

    @_captured_native_callback
    def stroke_path(self, ctx, path, stroke, ctm, cs, color, alpha, params):
        self._path_paint("stroke_path", path, stroke, ctm,
                         (ctx, path, stroke, ctm, cs, color, alpha, params))

    @_captured_native_callback
    def fill_shade(self, ctx, shade, ctm, alpha, params):
        native = _native_pdf.mupdf
        raw = native.ll_fz_bound_shade(shade, ctm)
        infinite = native.ll_fz_is_infinite_rect(raw)
        if type(infinite) is not int or infinite not in (0, 1):
            raise ValueError("SOURCE_NATIVE_SHADE_BOUNDS_UNPROVABLE")
        bound = None if infinite else _native_rect(raw, allow_empty=True)
        self.footprints.append({"seqno": self.seqno, "bounds": bound,
                                "empty": bound is None and not infinite, "inner": None})
        self.paint_calls.append((self.seqno, "fill_shade"))
        super().fill_shade(ctx, shade, ctm, alpha, params)

    def _image_paint(self, name, ctm, args):
        native = _native_pdf.mupdf
        # Keep the transformed rectangle alive while _native_rect reads its borrow.
        bounds_owner = native.fz_transform_rect(
            native.FzRect(0, 0, 1, 1), native.FzMatrix(ctm))
        bounds = _native_rect(bounds_owner.internal(), allow_empty=True)
        self.footprints.append({"seqno": self.seqno, "bounds": bounds,
                                "empty": bounds is None, "inner": None})
        self.paint_calls.append((self.seqno, name))
        getattr(_native_pdf.JM_new_texttrace_device, name)(self, *args)

    @_captured_native_callback
    def fill_image(self, ctx, image, ctm, alpha, params):
        self._image_paint("fill_image", ctm, (ctx, image, ctm, alpha, params))

    @_captured_native_callback
    def fill_image_mask(self, ctx, image, ctm, cs, color, alpha, params):
        self._image_paint("fill_image_mask", ctm, (ctx, image, ctm, cs, color, alpha, params))

    @_captured_native_callback
    def clip_path(self, ctx, path, even_odd, ctm, scissor):
        native = _native_pdf.mupdf
        # keep_path follows the vendor's own walker lifetime pattern; this
        # temporary wrapper releases its reference at callback exit.
        kept = native.FzPath(native.ll_fz_keep_path(path))
        is_rectangle = kept.fz_path_is_rect(native.FzMatrix(ctm))
        if type(is_rectangle) is not int or is_rectangle not in (0, 1):
            raise ValueError("SOURCE_NATIVE_CLIP_RECT_PREDICATE_INVALID")
        if is_rectangle:
            bound = native.ll_fz_bound_path(path, None, ctm)
            raw_bounds = (bound.x0, bound.y0, bound.x1, bound.y1)
            if not all(type(item) in (int, float) and math.isfinite(item) for item in raw_bounds):
                raise ValueError("SOURCE_NATIVE_CLIP_BOUNDS_INVALID")
            if fitz.Rect(raw_bounds).is_empty:
                # Native rectangles may be degenerate. No paint is certified
                # while this clip is active; a genuine later pop can restore it.
                self.active_clips.append({"kind": "empty-rectangle"})
            else:
                bounds = _native_rect(bound)
                self.active_clips.append({"kind": "rectangle", "bounds": bounds})
        else:
            boundaries = _native_boundaries(path, ctm)
            self.active_clips.append({"kind": "bounded-path" if boundaries else "unknown-path",
                                      "boundaries": boundaries, "even_odd": even_odd})

    @_captured_native_callback
    def clip_stroke_path(self, *args): self.active_clips.append({"kind": "stroke-path"})
    @_captured_native_callback
    def clip_text(self, *args): self.active_clips.append({"kind": "text"})
    @_captured_native_callback
    def clip_stroke_text(self, *args): self.active_clips.append({"kind": "stroke-text"})
    @_captured_native_callback
    def clip_image_mask(self, *args): self.active_clips.append({"kind": "image-mask"})

    @_captured_native_callback
    def pop_clip(self, *args):
        if not self.active_clips:
            raise ValueError("SOURCE_NATIVE_CLIP_STACK_UNBALANCED")
        self.active_clips.pop()

    @_captured_native_callback
    def begin_group(self, ctx, area, colorspace, isolated, knockout, blendmode, alpha):
        self.groups.append({"kind": "group", "bounds": _native_rect(area),
                            "colorspace_null": colorspace is None,
                            "colorspace_proof": _native_group_colorspace(colorspace), "isolated": isolated,
                            "knockout": knockout, "blendmode": blendmode, "alpha": alpha})

    @_captured_native_callback
    def end_group(self, *args):
        if not self.groups or self.groups[-1]["kind"] != "group":
            raise ValueError("SOURCE_NATIVE_GROUP_STACK_UNBALANCED")
        self.groups.pop()

    @_captured_native_callback
    def begin_tile(self, *args):
        self.groups.append({"kind": "tile"})
        return 0  # run content; never manufacture a cached native proof

    @_captured_native_callback
    def end_tile(self, *args):
        if not self.groups or self.groups[-1]["kind"] != "tile":
            raise ValueError("SOURCE_NATIVE_TILE_STACK_UNBALANCED")
        self.groups.pop()

    @_captured_native_callback
    def begin_mask(self, *args): self.mask_definitions.append("mask-definition")

    @_captured_native_callback
    def end_mask(self, *args):
        if not self.mask_definitions:
            raise ValueError("SOURCE_NATIVE_MASK_STACK_UNBALANCED")
        self.mask_definitions.pop()
        self.active_clips.append({"kind": "soft-mask"})

    def _capture_text(self, text, ctm, stroke, alpha, callback, args):
        before, seqno = len(self.out), self.seqno
        native = _native_pdf.mupdf
        glyph_spans = []
        raw_span = text.head
        while raw_span:
            span = native.FzTextSpan(raw_span)
            glyphs = []
            for index in range(span.m_internal.len):
                item = span.items(index)
                # Copy the actual native matrix, including positioned glyph
                # origin. The item/font references live only in this callback.
                trm = native.FzMatrix(span.trm().internal())
                trm.e, trm.f = item.x, item.y
                origin = native.fz_transform_point(
                    native.fz_make_point(item.x, item.y), native.FzMatrix(ctm))
                # A native bound_glyph/text bound can use the font's maximum
                # box (not this glyph's outline), notably for substitute CJK.
                # Use the native complete positioned outline instead. Its
                # actual font is the same one used by this paint callback;
                # substitute status does not invent a different font mapping.
                font = span.font()
                flags = native.ll_fz_font_flags(font.m_internal)
                if not native.ll_fz_font_ft_face(font.m_internal) or flags is None:
                    raise ValueError("SOURCE_NATIVE_FONT_OUTLINE_UNPROVABLE")
                for name in ("ft_stretch", "fake_bold", "fake_italic"):
                    flag = getattr(flags, name)
                    if type(flag) is not int or flag != 0:
                        raise ValueError("SOURCE_NATIVE_FONT_TRANSFORM_UNPROVABLE:" + name)
                if type(flags.ft_substitute) is not int or flags.ft_substitute not in (0, 1):
                    raise ValueError("SOURCE_NATIVE_FONT_SUBSTITUTE_FLAG_INVALID")
                self._require_outline_font(font)
                if type(item.gid) is not int or item.gid < 0:
                    raise ValueError("SOURCE_NATIVE_GLYPH_OUTLINE_UNPROVABLE")
                # Class-aware outline_glyph owns/releases its returned path.
                # NULL is unknown (e.g. Type3/bitmap), never empty paint.
                # A genuine non-NULL empty path is independently empty native
                # ink, including spaces; Unicode semantics are not consulted.
                outline = font.fz_outline_glyph(item.gid, trm)
                if not outline:
                    raise ValueError("SOURCE_NATIVE_GLYPH_OUTLINE_UNPROVABLE")
                bound = _native_rect(native.ll_fz_bound_path(
                    outline.m_internal, stroke, ctm), allow_empty=True)
                contours = tuple(bounds for _path, bounds in _native_contours(outline, stroke, ctm))
                single = native.FzText()
                single.fz_show_glyph_aux(
                    font, trm, item.adv, item.gid, item.ucs, item.cid,
                    span.m_internal.wmode, span.m_internal.bidi_level,
                    span.m_internal.markup_dir, span.m_internal.language)
                # Conservative native text bounds allocate the raster crop,
                # including native padding. They do not decide intersections.
                render_bounds = _native_rect(native.ll_fz_bound_text(
                    single.m_internal, stroke, ctm), allow_empty=True)
                glyphs.append({"binding": (item.ucs, item.gid, (origin.x, origin.y)),
                               "ink": bound, "render_bounds": render_bounds, "contours": contours})
            glyph_spans.append({"font": _native_pdf.JM_EscapeStrFromStr(
                _native_pdf.JM_font_name(span.font())), "glyphs": glyphs})
            raw_span = raw_span.next
        # A separate space-only text operation may have legitimate empty ink.
        # Still run the vendor callback and retain its exact paint sequence;
        # an empty target operation itself can never certify visibility.
        text_bounds = _native_rect(native.ll_fz_bound_text(text, stroke, ctm), allow_empty=True)
        callback(self, *args)
        operation = self.out[before:]
        if len(glyph_spans) != len(operation):
            raise ValueError("SOURCE_NATIVE_GLYPH_SPAN_BINDING_MISMATCH")
        glyph_records = []
        for captured_span, traced_span in zip(glyph_spans, operation):
            glyphs, chars = captured_span["glyphs"], traced_span["chars"]
            if captured_span["font"] != traced_span["font"] or len(glyphs) != len(chars):
                raise ValueError("SOURCE_NATIVE_GLYPH_SPAN_BINDING_MISMATCH")
            for glyph, char in zip(glyphs, chars):
                if glyph["binding"] != char[:3]:
                    raise ValueError("SOURCE_NATIVE_GLYPH_ITEM_BINDING_MISMATCH")
                glyph_records.append({"span": traced_span, "char": char, "ink": glyph["ink"],
                                      "render_bounds": glyph["render_bounds"], "contours": glyph["contours"]})
        # Containment uses ALL exact-bound glyph outlines in this operation.
        # bound_text adds raster padding and may include the font maximum box;
        # retain it for audit without treating that padding as painted ink.
        # Genuine empty outlines (e.g. spaces) add no ink. Unknown outlines
        # already failed above; they cannot disappear from this union.
        painted = [glyph["ink"] for glyph in glyph_records if glyph["ink"] is not None]
        ink = (min(bounds[0] for bounds in painted), min(bounds[1] for bounds in painted),
               max(bounds[2] for bounds in painted), max(bounds[3] for bounds in painted)) if painted else None
        # Plain values only. Native glyph ink includes annotation/tone contours
        # beyond layout metrics/advance; stroke uses its actual stroke state.
        self.draws.append({"seqno": seqno, "trace": operation, "ink": ink,
                           "text_bounds": text_bounds,
                           "glyphs": glyph_records,
                           "clips": tuple(dict(item) for item in self.active_clips),
                           "groups": tuple(dict(item) for item in self.groups),
                           "mask_definitions": tuple(self.mask_definitions), "alpha": alpha})

    def _require_outline_font(self, font):
        # ft_face alone also permits bitmap/color fonts. Inspect this native
        # rendering font's own immutable buffer, not a claimed caller format.
        # The retained owning font prevents pointer reuse during this capture.
        key = font.m_internal_value()
        if key in self._outline_fonts:
            return
        native = _native_pdf.mupdf
        buffer = font.m_internal.buffer
        if not buffer:
            raise ValueError("SOURCE_NATIVE_FONT_BUFFER_UNAVAILABLE")
        raw = native.fz_buffer_extract_copy(native.FzBuffer(native.ll_fz_keep_buffer(buffer)))
        if raw[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true"):
            face = font.m_internal.subfont
            if type(face) is not int or face < 0:
                raise ValueError("SOURCE_NATIVE_FONT_FACE_UNPROVABLE")
            with TTFont(io.BytesIO(raw), fontNumber=face, lazy=True) as parsed:
                tables = set(parsed.keys())
                unsupported = {"COLR", "CPAL", "CBDT", "CBLC", "EBDT", "EBLC", "EBSC",
                               "sbix", "SVG ", "bdat", "bloc", "fvar", "gvar", "CFF2", "VARC"}
                if unsupported & tables or not ({"glyf", "CFF "} & tables):
                    raise ValueError("SOURCE_NATIVE_COLOR_BITMAP_OR_VARIABLE_FONT_UNPROVABLE")
        elif not (len(raw) >= 4 and raw[0] == 1 and raw[1] == 0
                  and 4 <= raw[2] <= len(raw) and raw[3] in (1, 2, 3, 4)):
            # Raw CFF1 is the finite vector charstring format already supported
            # by the project. Other programs/containers never default outline.
            raise ValueError("SOURCE_NATIVE_FONT_FORMAT_UNPROVABLE")
        self._outline_fonts[key] = font

    @_captured_native_callback
    def fill_text(self, ctx, text, ctm, colorspace, color, alpha, color_params):
        self.paint_calls.append((self.seqno, "fill_text"))
        args = (ctx, text, ctm, colorspace, color, alpha, color_params)
        self._capture_text(text, ctm, None, alpha, _native_pdf.JM_new_texttrace_device.fill_text, args)

    @_captured_native_callback
    def stroke_text(self, ctx, text, stroke, ctm, colorspace, color, alpha, color_params):
        self.paint_calls.append((self.seqno, "stroke_text"))
        args = (ctx, text, stroke, ctm, colorspace, color, alpha, color_params)
        self._capture_text(text, ctm, stroke, alpha, _native_pdf.JM_new_texttrace_device.stroke_text, args)


def _capture_native_page(page, traces):
    native = _native_pdf.mupdf
    device = _CurrentClipTrace()
    closed = False
    try:
        native_page = native.FzPage(page.this) if isinstance(page.this, native.PdfPage) else page.this
        if not isinstance(native_page, native.FzPage):
            raise ValueError("SOURCE_NATIVE_PAGE_UNSUPPORTED")
        area = native.fz_bound_page(native_page)
        device.ptm = native.FzMatrix(1, 0, 0, -1, 0, area.y1)
        native.fz_run_page(native_page, device, native.FzMatrix(), native.FzCookie())
        native.fz_close_device(device)
        closed = True
        if device.capture_failure is not None:
            raise ValueError("SOURCE_NATIVE_CAPTURE_FAILED: " + device.capture_failure)
        # Unrelated size/spacewidth audit scalars have vendor float32 vs Python
        # double differences. Sequence/type/font/full chars are exact binding.
        fields = ("seqno", "type", "font", "chars")
        if ([tuple(span[key] for key in fields) for span in traces]
                != [tuple(span[key] for key in fields) for span in device.out]):
            raise ValueError("SOURCE_NATIVE_TRACE_BINDING_MISMATCH")
        if device.paint_calls != [(index, name) for index, (_seq, name)
                                  in enumerate(device.paint_calls)]:
            raise ValueError("SOURCE_NATIVE_PAINT_ORDER_UNPROVABLE")
        return {"draws": device.draws, "paint_calls": tuple(device.paint_calls),
                "footprints": device.footprints,
                "visibility": {}}
    finally:
        if not closed:
            native.fz_close_device(device)


def _native_clip_proof(analysis, seqno, source_span, source_char):
    """Preserve complete-operation page/clip/group containment, not metrics."""
    native = _native_pdf.mupdf
    canonical_page = analysis["page"].rect
    draws = [draw for draw in analysis["draws"] if draw["seqno"] == seqno]
    operation = [span for span in analysis["traces"] if span["seqno"] == seqno]
    if len(draws) != 1 or not operation or draws[0]["trace"] != operation:
        raise ValueError("SOURCE_NATIVE_PAINT_OPERATION_UNPROVABLE")
    draw = draws[0]
    if draw["ink"] is None:
        raise ValueError("SOURCE_NATIVE_TARGET_INK_EMPTY")
    ink = fitz.Rect(draw["ink"])
    if not canonical_page.contains(ink):
        raise ValueError("SOURCE_PAINT_CLIPPED_BY_PAGE")
    if draw["mask_definitions"]:
        raise ValueError("SOURCE_ACTIVE_MASK_UNPROVABLE")
    for clip in draw["clips"]:
        if clip["kind"] == "bounded-path":
            if not _boundary_contains(clip["boundaries"], clip["even_odd"], ink):
                raise ValueError("SOURCE_ACTIVE_CLIP_UNPROVABLE")
            continue
        if clip["kind"] != "rectangle":
            raise ValueError("SOURCE_ACTIVE_CLIP_UNPROVABLE")
        if not fitz.Rect(clip["bounds"]).contains(ink):
            raise ValueError("SOURCE_PAINT_CLIPPED")
    for group in draw["groups"]:
        # Explicit, finite opaque isolated Normal-group proof: inherited-null
        # or native builtin DeviceCMYK, no knockout, alpha 1, full ink in
        # its area. Other/missing/unknown group attributes never default
        # compatible. Form BBox clips use the same native rectangle proof.
        if (group["kind"] != "group"
                or group["colorspace_proof"] not in {"inherited-null", "builtin-device-cmyk"}
                or type(group["isolated"]) is not int or group["isolated"] != 1
                or type(group["knockout"]) is not int or group["knockout"] != 0
                or type(group["blendmode"]) is not int
                or group["blendmode"] != native.FZ_BLEND_NORMAL
                or type(group["alpha"]) not in (int, float) or group["alpha"] != 1
                or type(draw["alpha"]) not in (int, float) or draw["alpha"] != 1
                or not fitz.Rect(group["bounds"]).contains(ink)):
            raise ValueError("SOURCE_ACTIVE_GROUP_UNPROVABLE")
    sources = [(index, glyph) for index, glyph in enumerate(draw["glyphs"])
               if glyph["span"] == source_span and glyph["char"] == source_char]
    if len(sources) != 1 or sources[0][1]["ink"] is None:
        raise ValueError("SOURCE_NATIVE_OCCURRENCE_INK_UNPROVABLE")
    source_index, source = sources[0]
    if (type(draw["alpha"]) not in (int, float) or draw["alpha"] != 1
            or source["render_bounds"] is None):
        raise ValueError("SOURCE_NATIVE_OPAQUE_PAINT_UNPROVABLE")
    return source_index, source


_FORWARDED_CALLBACKS = (
    "fill_path", "stroke_path", "clip_path", "clip_stroke_path", "clip_text",
    "clip_stroke_text", "ignore_text", "fill_shade", "fill_image", "fill_image_mask",
    "clip_image_mask", "pop_clip", "begin_mask", "end_mask", "begin_group", "end_group",
    "begin_layer", "end_layer", "render_flags", "set_default_colorspaces",
    "begin_structure", "end_structure", "begin_metatext", "end_metatext",
)
_PAINT_CALLBACKS = frozenset(("fill_path", "stroke_path", "fill_text", "stroke_text",
                              "ignore_text", "fill_shade", "fill_image", "fill_image_mask"))


class _SourceContributionDevice(_native_pdf.JM_new_texttrace_device):
    """Finite native replay: omit exactly one bound glyph and/or later paint.

    Native devices retain their own clipping, compositing and colorspace state.
    This device forwards those calls; it does not interpret PDF operators or
    reconstruct RGB blending. Future/unsupported callbacks record refusal.
    """
    def __init__(self, analysis, draw, mask, seqno, ordinal, *, remove, prefix):
        super().__init__([])
        self.analysis, self.draw, self.mask = analysis, draw, mask
        self.target_seq, self.target_ordinal = seqno, ordinal
        self.remove, self.prefix, self.selected = remove, prefix, 0
        self.capture_failure = None
        self.contour_masks = []
        names = {name.removeprefix("use_virtual_") for name in dir(_native_pdf.mupdf.FzDevice2)
                 if name.startswith("use_virtual_")} - {"close_device", "drop_device"}
        for name in names:
            getattr(self, "use_virtual_" + name)()

    def _paint(self, name):
        calls = self.analysis["paint_calls"]
        if self.seqno >= len(calls) or calls[self.seqno] != (self.seqno, name):
            raise ValueError("SOURCE_RENDER_PAINT_ORDER_MISMATCH")
        return self.seqno

    def _forward(self, name, ctx, args):
        native = _native_pdf.mupdf
        seqno = self._paint(name) if name in _PAINT_CALLBACKS else None
        if seqno is not None or name in ("begin_layer", "end_layer"):
            # The installed vendor's paint callbacks own both seqno and trace
            # state (notably stroke_path's linewidth). Synchronize even paint
            # omitted from the prefix image; never copy expected audit values.
            getattr(_native_pdf.JM_new_texttrace_device, name)(self, ctx, *args)
            if seqno is not None and self.seqno != seqno + 1:
                raise ValueError("SOURCE_RENDER_PAINT_SEQUENCE_MISMATCH")
        if self.prefix and seqno is not None and seqno > self.target_seq:
            return
        if name == "end_mask":
            native.ll_fz_end_mask_tr(self.draw.m_internal, *args)
        else:
            getattr(native, "ll_fz_" + name)(self.draw.m_internal, *args)

    def _text(self, name, ctx, text, ctm, stroke, colorspace, color, alpha, params):
        native = _native_pdf.mupdf
        seqno = self._paint(name)
        retained = text
        selected = None
        before = len(self.out)
        callback = getattr(_native_pdf.JM_new_texttrace_device, name)
        args = ((ctx, text, stroke, ctm, colorspace, color, alpha, params) if stroke is not None
                else (ctx, text, ctm, colorspace, color, alpha, params))
        callback(self, *args)
        if self.seqno != seqno + 1:
            raise ValueError("SOURCE_RENDER_PAINT_SEQUENCE_MISMATCH")
        if seqno == self.target_seq:
            expected = [span for span in self.analysis["traces"] if span["seqno"] == seqno]
            # Prior text has also executed its real vendor callback. Compare
            # this operation's complete newly emitted trace, not the whole log.
            if self.out[before:] != expected:
                raise ValueError("SOURCE_RENDER_EXACT_OPERATION_MISMATCH")
            filtered, selected = native.FzText(), native.FzText()
            raw_span, ordinal = text.head, 0
            while raw_span:
                span = native.FzTextSpan(raw_span)
                for index in range(span.m_internal.len):
                    item = span.items(index)
                    trm = native.FzMatrix(span.trm().internal())
                    trm.e, trm.f = item.x, item.y
                    args = (span.font(), trm, item.adv, item.gid, item.ucs, item.cid,
                            span.m_internal.wmode, span.m_internal.bidi_level,
                            span.m_internal.markup_dir, span.m_internal.language)
                    if ordinal == self.target_ordinal:
                        self.selected += 1
                        selected.fz_show_glyph_aux(*args)
                        if not self.remove and not self.prefix:
                            outline = span.font().fz_outline_glyph(item.gid, trm)
                            if not outline:
                                raise ValueError("SOURCE_NATIVE_GLYPH_OUTLINE_UNPROVABLE")
                            contours = _native_contours(outline, stroke, ctm)
                            source = next(draw for draw in self.analysis["draws"]
                                          if draw["seqno"] == seqno)["glyphs"][ordinal]
                            if tuple(bounds for _path, bounds in contours) != source["contours"]:
                                raise ValueError("SOURCE_NATIVE_CONTOUR_BINDING_UNPROVABLE")
                            if not contours or len(contours) > 256:
                                raise ValueError("SOURCE_NATIVE_CONTOUR_COUNT_UNPROVABLE")
                            box = native.fz_round_rect(native.FzRect(
                                *(value * 4 for value in source["render_bounds"])))
                            area = (box.x1-box.x0) * (box.y1-box.y0)
                            # Refuse before allocating any component masks.
                            # Together with the per-canvas 1m-pixel cap, this
                            # limits retained contour masks to 64 MB rather
                            # than relying on a late allocation failure.
                            if area <= 0 or area * len(contours) > 16_000_000:
                                raise ValueError("SOURCE_RENDER_CONTOUR_BUDGET_UNPROVABLE")
                            for path, bounds in contours:
                                # A finite 4x mask cannot certify an independent
                                # subpixel component merely because the body's
                                # pixels remain visible. This minimum applies
                                # equally to outer and inner contours.
                                if ((bounds[2]-bounds[0])*4 < 1
                                        or (bounds[3]-bounds[1])*4 < 1):
                                    raise ValueError("SOURCE_NATIVE_CONTOUR_SUBPIXEL_UNPROVABLE")
                                pixmap, device, _ = _new_visibility_canvas(source["render_bounds"], mask=True)
                                try:
                                    _draw_contour(device, path, stroke, ctm, params)
                                finally:
                                    native.fz_close_device(device)
                                self.contour_masks.append(_visibility_pixels(pixmap))
                    if ((not self.prefix or ordinal <= self.target_ordinal)
                            and (not self.remove or ordinal != self.target_ordinal)):
                        filtered.fz_show_glyph_aux(*args)
                    ordinal += 1
                raw_span = raw_span.next
            if self.remove or self.prefix:
                retained = filtered.m_internal
        if not self.prefix or seqno <= self.target_seq:
            self._draw_text(self.draw, retained, ctm, stroke, colorspace, color, alpha, params)
        if selected is not None:
            # Native coverage, not any nonwhite content in a locator crop.
            self._draw_text(self.mask, selected.m_internal, ctm, stroke,
                            native.fz_device_rgb().m_internal, (0., 0., 0., 0.), 1., params,
                            direct_color=True)

    @staticmethod
    def _draw_text(device, text, ctm, stroke, colorspace, color, alpha, params, *, direct_color=False):
        native = _native_pdf.mupdf
        if stroke is not None:
            if not direct_color:
                native.ll_fz_stroke_text(device.m_internal, text, stroke, ctm,
                                        colorspace, color, alpha, params)
            else:
                buffer = native.new_floats(4)
                try:
                    for index, value in enumerate(color):
                        native.floats_setitem(buffer, index, value)
                    native.ll_fz_stroke_text(device.m_internal, text, stroke, ctm,
                                            colorspace, buffer, alpha, params)
                finally:
                    native.delete_floats(buffer)
        else:
            if not direct_color:
                if native.ll_fz_colorspace_n(colorspace) not in (1, 2, 3, 4):
                    raise ValueError("SOURCE_RENDER_COLORSPACE_UNPROVABLE")
                # Native same-colorspace conversion copies the colorants.
                # Independent vendor rendering must still match byte-exactly.
                color = native.ll_fz_convert_color(colorspace, color, colorspace, None, params)
            native.ll_fz_fill_text2(device.m_internal, text, ctm, colorspace, *color, alpha, params)

    def fill_text(self, ctx, text, ctm, cs, color, alpha, params):
        try:
            self._text("fill_text", ctx, text, ctm, None, cs, color, alpha, params)
        except BaseException as exc:
            self.capture_failure = str(exc) or type(exc).__name__

    def stroke_text(self, ctx, text, stroke, ctm, cs, color, alpha, params):
        try:
            self._text("stroke_text", ctx, text, ctm, stroke, cs, color, alpha, params)
        except BaseException as exc:
            self.capture_failure = str(exc) or type(exc).__name__


def _forwarded_callback(name):
    def callback(self, ctx, *args):
        try:
            return self._forward(name, ctx, args)
        except BaseException as exc:
            self.capture_failure = name + ": " + (str(exc) or type(exc).__name__)
    return callback


def _unsupported_callback(name):
    def callback(self, *args):
        self.capture_failure = "SOURCE_RENDER_UNSUPPORTED_CALLBACK:" + name
        return 0 if name == "begin_tile" else None
    return callback


for _callback_name in (name.removeprefix("use_virtual_") for name in dir(_native_pdf.mupdf.FzDevice2)
                       if name.startswith("use_virtual_")):
    if _callback_name not in ("fill_text", "stroke_text", "close_device", "drop_device"):
        setattr(_SourceContributionDevice, _callback_name,
                _forwarded_callback(_callback_name) if _callback_name in _FORWARDED_CALLBACKS
                else _unsupported_callback(_callback_name))


def _new_visibility_canvas(bounds, *, mask=False):
    native = _native_pdf.mupdf
    box = native.fz_round_rect(native.FzRect(*(value * 4 for value in bounds)))
    if ((box.x1 - box.x0) * (box.y1 - box.y0) > 1_000_000
            or box.x1 <= box.x0 or box.y1 <= box.y0):
        raise ValueError("SOURCE_RENDER_AREA_UNPROVABLE")
    pixmap = native.fz_new_pixmap_with_bbox(native.fz_device_rgb(), box, native.FzSeparations(), 1)
    if mask:
        native.ll_fz_clear_pixmap(pixmap.m_internal)
    else:
        native.ll_fz_clear_pixmap_with_value(pixmap.m_internal, 255)
    device = native.fz_new_draw_device(native.FzMatrix(4, 0, 0, 4, 0, 0), pixmap)
    return pixmap, device, box


def _visibility_pixels(pixmap):
    wrapper = fitz.Pixmap(pixmap)
    if wrapper.n != 4 or wrapper.alpha != 1 or wrapper.stride != wrapper.width * 4:
        raise ValueError("SOURCE_RENDER_PIXEL_FORMAT_UNPROVABLE")
    return wrapper.samples


def _draw_contour(device, path, stroke, ctm, params):
    native = _native_pdf.mupdf
    color = native.new_floats(4)
    try:
        for index in range(4):
            native.floats_setitem(color, index, 0.)
        if stroke is None:
            native.ll_fz_fill_path(device.m_internal, path.m_internal, 0, ctm,
                                  native.fz_device_rgb().m_internal, color, 1., params)
        else:
            native.ll_fz_stroke_path(device.m_internal, path.m_internal, stroke, ctm,
                                    native.fz_device_rgb().m_internal, color, 1., params)
    finally:
        native.delete_floats(color)


def _later_ink_may_touch(analysis, seqno, ordinal, source):
    """Continuous native geometry prevents finite raster misses being PASS.

    Bounds only establish suspicion, never confirmed occlusion. A rectangle
    stroke's independently proven empty interior excludes a surrounding frame.
    Other unknown/possibly intersecting footprints remain unprovable.
    """
    contours = [fitz.Rect(bounds) for bounds in source["contours"]]
    if not contours:
        return True
    for draw in analysis["draws"]:
        if draw["seqno"] < seqno:
            continue
        for index, glyph in enumerate(draw["glyphs"]):
            if draw["seqno"] == seqno and index <= ordinal:
                continue
            if glyph["ink"] is not None and any(fitz.Rect(glyph["ink"]).intersects(b)
                                                for b in contours):
                return True
    for paint in analysis["footprints"]:
        if paint["seqno"] <= seqno or paint["empty"]:
            continue
        if paint["bounds"] is None:
            return True
        boundaries = paint.get("stroke_boundaries")
        if boundaries is not None:
            if all(_boundary_disjoint(part["bounds"], bounds)
                   for part in boundaries for bounds in contours):
                continue
            return True
        inner = fitz.Rect(paint["inner"]) if paint["inner"] is not None else None
        for bounds in contours:
            if inner is not None and inner.contains(bounds):
                continue
            if fitz.Rect(paint["bounds"]).intersects(bounds):
                return True
    return False


def _native_visibility_proof(analysis, seqno, ordinal, glyph):
    key = seqno, ordinal
    if key in analysis["visibility"]:
        return dict(analysis["visibility"][key])
    native = _native_pdf.mupdf
    page = analysis["page"]
    canvases, readers = [], []
    closed = set()
    try:
        native_page = native.FzPage(page.this) if isinstance(page.this, native.PdfPage) else page.this
        area = native.fz_bound_page(native_page)
        for remove, prefix in ((False, False), (True, False), (False, True), (True, True)):
            pixmap, draw, box = _new_visibility_canvas(glyph["render_bounds"])
            mask, mask_device, _ = _new_visibility_canvas(glyph["render_bounds"], mask=True)
            canvases.append((pixmap, draw, mask, mask_device))
            reader = _SourceContributionDevice(analysis, draw, mask_device, seqno, ordinal,
                                               remove=remove, prefix=prefix)
            readers.append(reader)
            reader.ptm = native.FzMatrix(1, 0, 0, -1, 0, area.y1)
            native.fz_run_page(native_page, reader, native.FzMatrix(), native.FzCookie())
            native.fz_close_device(reader)
            closed.add(id(reader))
            for device in (draw, mask_device):
                native.fz_close_device(device)
                closed.add(id(device))
            if (reader.capture_failure is not None or reader.selected != 1
                    or reader.seqno != len(analysis["paint_calls"])):
                raise ValueError("SOURCE_RENDER_CAPTURE_UNPROVABLE:" + str(reader.capture_failure))
        full, without, prefix, prefix_without = [_visibility_pixels(item[0]) for item in canvases]
        masks = [_visibility_pixels(item[2]) for item in canvases]
        if any(mask != masks[0] for mask in masks[1:]):
            raise ValueError("SOURCE_RENDER_COVERAGE_UNPROVABLE")
        # The reference includes the same page/annotation/widget domain as
        # fz_run_page. No annotation is hidden to manufacture a valid source.
        reference = page.get_pixmap(matrix=fitz.Matrix(4, 4),
                                    clip=fitz.Rect(box.x0/4, box.y0/4, box.x1/4, box.y1/4),
                                    alpha=False, annots=True)
        rgb = bytes(value for index, value in enumerate(full) if index % 4 != 3)
        if reference.n != 3 or rgb != reference.samples:
            raise ValueError("SOURCE_RENDER_VENDOR_EQUIVALENCE_UNPROVABLE")
        pixels = [offset for offset in range(0, len(masks[0]), 4) if masks[0][offset+3] > 0]
        if not pixels:
            raise ValueError("SOURCE_RENDER_GLYPH_COVERAGE_EMPTY")
        # Every independent native outline has its own sampled mask; body or
        # neighboring pixels cannot satisfy a missing component. Inner contours
        # may represent holes; at least their actual boundary must be supported
        # by the bound source glyph's mask, rather than Unicode assumptions.
        if (len(readers[0].contour_masks) != len(glyph["contours"])
                or any(not any(contour[offset+3] and masks[0][offset+3]
                               for offset in range(0, len(contour), 4))
                       for contour in readers[0].contour_masks)):
            raise ValueError("SOURCE_RENDER_CONTOUR_COVERAGE_UNPROVABLE")
        background = {prefix_without[offset:offset+3] for offset in pixels}
        if len(background) != 1:
            result = {"readable": False, "overlay_status": "SUSPECTED",
                      "reason": "SOURCE_PREFIX_BACKGROUND_UNPROVABLE"}
        elif any(prefix[offset:offset+3] == prefix_without[offset:offset+3] for offset in pixels):
            result = {"readable": False, "overlay_status": "SUSPECTED",
                      "reason": "SOURCE_COMPLETE_CONTRAST_UNPROVABLE"}
        elif any(full[offset:offset+3] != prefix[offset:offset+3]
                 or without[offset:offset+3] != prefix_without[offset:offset+3] for offset in pixels):
            result = {"readable": False, "overlay_status": "CONFIRMED",
                      "reason": "SOURCE_OVERPAINT_CONFIRMED"}
        elif _later_ink_may_touch(analysis, seqno, ordinal, glyph):
            result = {"readable": False, "overlay_status": "SUSPECTED",
                      "reason": "SOURCE_LATER_INK_INTERSECTION_UNPROVABLE"}
        else:
            result = {"readable": True, "overlay_status": "NONE", "reason": "SOURCE_FINAL_VISIBILITY_PROVED"}
        result["source_coverage_pixels"] = len(pixels)
        # Surface confirmation uses a different evidence lane. Nonzero native
        # source contribution is necessary, never proof of human readability.
        result["surface_source_contribution_pixels"] = sum(
            full[offset:offset+3] != without[offset:offset+3] for offset in pixels)
        result["surface_replay_sha256"] = {
            name: hashlib.sha256(raw).hexdigest() for name, raw in
            (("full", full), ("without", without), ("prefix", prefix), ("prefix_without", prefix_without))}
        analysis["visibility"][key] = dict(result)
        return result
    finally:
        # Even an exception during close is a refusal. Native callback errors
        # cannot be swallowed into a default success or an empty mask.
        for reader in readers:
            if id(reader) not in closed:
                native.fz_close_device(reader)
        for _pixmap, draw, _mask, mask_device in canvases:
            for device in (draw, mask_device):
                if id(device) not in closed:
                    native.fz_close_device(device)


def _result(readable=False, donor=False, reason="SOURCE_UNPROVABLE", **metadata):
    return {"location_readable": readable, "donor_eligible": donor,
            "reason": reason, "overlay_status": "NONE" if readable else "SUSPECTED", **metadata}


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
    # pdf is a location hint: collect_manifest can retain the original path in
    # source_record while binding the entry to a moved copy. Only the validated
    # source SHA plus read_source/_seal_bytes below can prove its content. Keep
    # every identity claim (including pdf_name) independently contradictory.
    for key in ("pdf_name", "font", "zhuyin_component_id"):
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


def _warning_bounds(entry):
    try:
        bounds = [entry.get(key) for key in _BOX_KEYS]
        if (all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
                and not fitz.Rect(bounds).is_empty and not fitz.Rect(bounds).is_infinite):
            return bounds
    except Exception:
        pass
    return None


def _annotation_intersects(page, ink):
    # Native page replay and the independent preview both include annotations.
    # Declared related annotation/widget bounds are only suspicion, not proof
    # of actual coverage. Unavailable/invalid bounds never default unrelated.
    for iterator in (page.annots(), page.widgets()):
        for item in iterator or ():
            bounds = _native_rect(item.rect)
            if fitz.Rect(ink).intersects(fitz.Rect(bounds)):
                return True
    return False


def _source_visual_safety(operation, entry, *, pdf=None, document=None, page=None):
    values, source = _tuple(entry)
    if page is not None:
        if document is None:
            document = page.parent
        if page.parent is not document or page.number != values["physical_page"] - 1:
            raise ValueError("SOURCE_PAGE_OWNERSHIP_MISMATCH")
    if pdf is not None:
        raw = (bytes(pdf) if isinstance(pdf, (bytes, bytearray))
               else operation.read_source(pdf, values["pdf_sha256"]))
    elif document is not None:
        raw = _document_bytes(document)
    else:
        if not (entry.get("pdf") or source.get("pdf")):
            raise ValueError("SOURCE_PDF_MISSING")
        from actual_review import _resolve_pdf_path
        path = _resolve_pdf_path(entry) if entry.get("pdf") else source["pdf"]
        raw = operation.read_source(path, values["pdf_sha256"])
    raw = operation._seal_bytes(raw, values["pdf_sha256"])
    if document is not None and _document_bytes(document) != raw:
        raise ValueError("SOURCE_DOCUMENT_BYTES_MISMATCH")
    analysis = operation._page(raw, values["pdf_sha256"], values["physical_page"])
    page, document = analysis["page"], analysis["document"]
    target = fitz.Rect(values["bbox"])
    if target.is_empty:
        from actual_render_geometry import EXACT_VERTICAL_CFF_PDF, exact_vertical_cff_location
        if values["pdf_sha256"] != EXACT_VERTICAL_CFF_PDF:
            raise ValueError("SOURCE_BBOX_EMPTY")
        from actual_review import _resolve_pdf_path
        path = (pdf if isinstance(pdf, (str, Path)) else
                _resolve_pdf_path({**entry, "pdf": entry.get("pdf") or source.get("pdf")}))
        target = fitz.Rect(exact_vertical_cff_location(Path(path), entry, digest=values["pdf_sha256"])["whole"])
    if not page.rect.contains(target):
        raise ValueError("SOURCE_BBOX_OUTSIDE_PAGE")
    lookup, traces = analysis["lookup"], analysis["traces"]
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
    span, source_char = matches[0]
    if span.get("type") not in (0, 1) or span.get("opacity", 0) <= 0:
        raise ValueError("SOURCE_DRAW_NOT_VISIBLE")
    seqno = _index(span.get("seqno"))
    for other in traces:
        if other.get("seqno", -1) < seqno or other.get("type") == 3:
            continue
        xref, *_ = resolve_font(lookup, normalize_basefont(other.get("font")))
        for char in other.get("chars", ()):
            if (all(abs(float(a)-b) <= .001 for a, b in zip(char[3], values["bbox"]))
                    and (char[0] != ord(values["char"]) or char[1] != values["glyph_id"]
                         or xref != values["font_xref"])):
                return _result(reason="SOURCE_SLOT_CONTRADICTION", overlay_status="SUSPECTED",
                               warning_bbox=list(values["bbox"]), source_seqno=seqno)
    ordinal, glyph = _native_clip_proof(analysis, seqno, span, source_char)
    exact = _exact_donor(document, values, source, entry)
    if _annotation_intersects(page, glyph["ink"]):
        return _result(reason="SOURCE_ANNOTATION_INTERSECTION_UNPROVABLE", overlay_status="SUSPECTED",
                       warning_bbox=list(values["bbox"]), source_seqno=seqno)
    visibility = _native_visibility_proof(analysis, seqno, ordinal, glyph)
    if not visibility["readable"]:
        return _result(reason=visibility["reason"], overlay_status=visibility["overlay_status"],
                       warning_bbox=list(values["bbox"]), source_seqno=seqno,
                       source_coverage_pixels=visibility["source_coverage_pixels"])
    return _result(True, exact,
                   "SOURCE_DRAW_UNCONTRADICTED" if exact else "SOURCE_LOCATION_ONLY_NO_EXACT_PROOF",
                   warning_bbox=None, source_seqno=seqno,
                   source_coverage_pixels=visibility["source_coverage_pixels"])


def source_visual_safety(entry, *, pdf=None, document=None, page=None):
    """Current actual-only source proof with no caller eligibility flags.

    An explicit operation shares sealed bytes/native page analysis. Every call
    still validates independent source claims; writers must recheck_sources
    before their first physical write. A standalone call rechecks before return.
    Diagnostics are transient; warning_bbox never authorizes confirmation.
    """
    try:
        current = _CURRENT_OPERATION.get()
        if current is not None:
            current._require_owner()
            result = _source_visual_safety(current, entry, pdf=pdf, document=document, page=page)
        else:
            with source_visual_operation() as operation:
                result = _source_visual_safety(operation, entry, pdf=pdf, document=document, page=page)
                operation.recheck_sources()
        if entry.get('manual_surface_actual') is not None:
            result = {**result,'donor_eligible':False,'surface_evidence_is_occurrence_local':True}
        return result
    except Exception as exc:
        return _result(reason=str(exc) or type(exc).__name__, warning_bbox=_warning_bounds(entry))


def _surface_plain(value):
    """Finite canonical native summaries; never serialize object repr/pointers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('SURFACE_NONFINITE_NATIVE_VALUE')
        return value
    if isinstance(value, (fitz.Point, fitz.Rect, fitz.Quad, fitz.Matrix)):
        return [_surface_plain(item) for item in value]
    if isinstance(value, (tuple, list)):
        return [_surface_plain(item) for item in value]
    if isinstance(value, dict) and all(type(key) is str for key in value):
        return {key: _surface_plain(item) for key, item in value.items()}
    raise ValueError('SURFACE_UNKNOWN_NATIVE_SUMMARY_TYPE')


def surface_visual_proof(entry, *, pdf, session_id):
    """Bind a final unit for explicit human reading, without qualifying a donor.

    The machine proves the slot, known native composition and immutable render.
    It does not identify a Chinese body or pronounce the resulting surface.
    The separate writer requires whole-unit/character/input attestations.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError('SURFACE_SESSION_MISSING')
    with source_visual_operation() as operation:
        raw_safety = _source_visual_safety(operation, entry, pdf=pdf)
        if raw_safety['reason'] not in {
                'SOURCE_OVERPAINT_CONFIRMED', 'SOURCE_DRAW_UNCONTRADICTED',
                'SOURCE_LOCATION_ONLY_NO_EXACT_PROOF'}:
            raise ValueError('SURFACE_SOURCE_REFUSED: ' + raw_safety['reason'])
        values, source = _tuple(entry)
        raw = operation.read_source(pdf, values['pdf_sha256'])
        analysis = operation._page(raw, values['pdf_sha256'], values['physical_page'])
        matches = []
        for span in analysis['traces']:
            xref, *_ = resolve_font(analysis['lookup'], normalize_basefont(span.get('font')))
            if xref != values['font_xref']:
                continue
            for char in span.get('chars', ()):
                if (char[0] == ord(values['char']) and char[1] == values['glyph_id']
                        and all(abs(float(a)-b) <= .001 for a, b in zip(char[3], values['bbox']))):
                    matches.append((span, char))
        if len(matches) != 1:
            raise ValueError('SURFACE_SOURCE_DRAW_NOT_UNIQUE')
        span, char = matches[0]
        seqno = _index(span['seqno'])
        ordinal, glyph = _native_clip_proof(analysis, seqno, span, char)
        visibility = _native_visibility_proof(analysis, seqno, ordinal, glyph)
        if visibility['surface_source_contribution_pixels'] <= 0:
            raise ValueError('SURFACE_SOURCE_FULLY_OCCLUDED')
        page = analysis['page']
        composition = _surface_plain({
            'paint_calls': analysis['paint_calls'], 'traces': analysis['traces'],
            'drawings': page.get_drawings(extended=True), 'bboxlog': page.get_bboxlog(layers=True)})
        scale = 1.5
        if page.rect.width*page.rect.height*scale*scale > 12_000_000:
            raise ValueError('SURFACE_PAGE_RENDER_ALLOCATION_EXCEEDED')
        target = (fitz.Rect(glyph['render_bounds']) + (-8, -8, 8, 8)) & page.rect
        if target.is_empty:
            raise ValueError('SURFACE_TARGET_EMPTY')
        full = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=True).tobytes('png')
        crop = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=target, alpha=False, annots=True).tobytes('png')
        render = {'page_scale': scale, 'target_scale': 4, 'annots': True,
                  'target_rect': list(target),
                  'page_png': base64.b64encode(full).decode('ascii'),
                  'target_png': base64.b64encode(crop).decode('ascii'),
                  'page_sha256': hashlib.sha256(full).hexdigest(),
                  'target_sha256': hashlib.sha256(crop).hexdigest()}
        proof = {'schema': 'surface-visual-proof/1', 'session_id': session_id,
                 'source': _surface_plain(values), 'composition': composition,
                 'source_seqno': seqno, 'glyph_ordinal': ordinal,
                 'source_coverage_pixels': visibility['source_coverage_pixels'],
                 'source_contribution_pixels': visibility['surface_source_contribution_pixels'],
                 'replay_sha256': visibility['surface_replay_sha256'], 'render': render,
                 'raw_reason': raw_safety['reason'], 'raw_overlay_status': raw_safety['overlay_status'],
                 'automatic_actual': str(entry.get('actual_raw') or source.get('實際注音') or entry.get('actual') or ''),
                 'donor_eligible': False}
        operation.recheck_sources()
        proof['proof_sha256'] = hashlib.sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        return proof
