from __future__ import annotations

import csv
import hashlib
import re
from io import BytesIO
from pathlib import Path
from typing import Iterable

from fontTools.cffLib import CFFFontSet
from fontTools.pens.recordingPen import RecordingPen

from exact_glyph_identity import (
    CFF_GLYPH_SHA256,
    GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
)
from global_exact_glyph_library import (
    GlobalExactGlyphSnapshot,
    canonical_global_exact_identity,
    resolve_exact_glyph_reuse,
)

# The embedded CFF fonts in this textbook family use a 1500-unit wide glyph:
# Han character at the left, Bopomofo column at ~x=1000-1329, tone mark at >=1330.
ANNOTATION_X_MIN = 1000
TONE_X_MIN = 1330
NEUTRAL_MAX_W = 90
NEUTRAL_MAX_H = 90

STYLE_PATTERNS = [
    ("BIAOKAI_W5", re.compile(r"^DFBiaoKai(?:ZhuIn|PoIn[123])-W5$", re.I)),
    ("YUAN_W3W5", re.compile(r"^DFYuan(?:ZhuIn|PoIn[123])-W[35]$", re.I)),
    ("YUAN_W7", re.compile(r"^DFYuan(?:ZhuIn|PoIn[123])-W7$", re.I)),
    ("HEI_W5", re.compile(r"^DFHei(?:ZhuIn|PoIn[123])-W5$", re.I)),
    ("HEI_W7", re.compile(r"^DFHei(?:ZhuIn|PoIn[123])-W7$", re.I)),
    # v4.3: 康軒 DFKaiChuIn CFF whole-glyph family.  This is a style label
    # for exact symbol signatures only; admission to CFF structural scanning
    # is no longer gated by font name in export_zhuyin_readings.py.
    ("KAICHU_MD", re.compile(r"^DFKaiChuIn-Md-(?:BPMF|PoIn[123])-BF$", re.I)),
]


def cff_style_group(font_name: str) -> str:
    for group, rx in STYLE_PATTERNS:
        if rx.search(str(font_name or "")):
            return group
    return ""


def load_cff_symbol_map(path: Path):
    out = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            group = (row.get("style_group") or "").strip()
            sig = (row.get("signature") or "").strip()
            symbol = (row.get("bopomofo_symbol") or "").strip()
            if group and sig and symbol:
                out[(group, sig)] = {
                    "symbol": symbol,
                    "verification": (row.get("verification") or "").strip(),
                    "notes": (row.get("notes") or "").strip(),
                }
    return out


def _split_contours(recording):
    out, cur = [], []
    for op, args in recording:
        if op == "moveTo":
            if cur:
                out.append(cur)
            cur = [(op, args)]
        else:
            if cur:
                cur.append((op, args))
            if op in ("closePath", "endPath") and cur:
                out.append(cur)
                cur = []
    if cur:
        out.append(cur)
    return out


def _points(contour):
    return [
        a
        for _op, args in contour
        for a in args
        if isinstance(a, tuple)
        and len(a) == 2
        and isinstance(a[0], (int, float))
        and isinstance(a[1], (int, float))
    ]


def _bounds(contour):
    pts = _points(contour)
    if not pts:
        return None
    xs = [x for x, _y in pts]
    ys = [y for _x, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _group_by_y(contours):
    """Merge contours that belong to the same vertically stacked Bopomofo symbol."""
    items = []
    for c in contours:
        bb = _bounds(c)
        if bb:
            items.append((bb[1], bb[3], c, bb))
    items.sort(key=lambda z: -z[1])
    groups = []
    for item in items:
        y0, y1 = item[:2]
        best, best_overlap = None, -1.0
        for i, group in enumerate(groups):
            gy0 = min(x[0] for x in group)
            gy1 = max(x[1] for x in group)
            overlap = min(y1, gy1) - max(y0, gy0)
            min_height = min(max(1.0, y1 - y0), max(1.0, gy1 - gy0))
            if overlap > max(5.0, 0.15 * min_height) and overlap > best_overlap:
                best, best_overlap = i, overlap
        if best is None:
            groups.append([item])
        else:
            groups[best].append(item)
    groups.sort(key=lambda g: -max(x[1] for x in g))
    return [[x[2] for x in g] for g in groups]


def _signature(group) -> str:
    pts = [p for contour in group for p in _points(contour)]
    if not pts:
        return ""
    minx = min(x for x, _y in pts)
    miny = min(y for _x, y in pts)
    arr = []
    for contour in group:
        converted = []
        for op, args in contour:
            new_args = []
            for a in args:
                if (
                    isinstance(a, tuple)
                    and len(a) == 2
                    and isinstance(a[0], (int, float))
                    and isinstance(a[1], (int, float))
                ):
                    new_args.append((round(a[0] - minx, 3), round(a[1] - miny, 3)))
                else:
                    new_args.append(a)
            converted.append((op, tuple(new_args)))
        arr.append(tuple(converted))
    return hashlib.sha1(repr(tuple(arr)).encode()).hexdigest()


def _tone_correlation(contours) -> float | None:
    # Pearson correlation of the tone contour's x/y control points.  In these
    # fonts: rising tone ≈ +0.9, falling tone ≈ -0.9, third tone ≈ +0.1..+0.45.
    pts = [p for c in contours for p in _points(c)]
    if len(pts) < 2:
        return None
    xs = [float(x) for x, _y in pts]
    ys = [float(y) for _x, y in pts]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sx2 = sum(x * x for x in dx)
    sy2 = sum(y * y for y in dy)
    if sx2 <= 0 or sy2 <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / ((sx2 * sy2) ** 0.5)


def cff_glyph_outline_sha256(recording) -> str:
    """Exact SHA-256 of the complete rendered CFF glyph outline program.

    User-learned CFF truth is keyed by this full-glyph outline, never by the
    Bopomofo annotation signature alone.  This prevents a visually/semantically
    different Han glyph that happens to reuse the same annotation component
    signatures from inheriting a human/GPT correction.  No fuzzy matching or
    character/expected information participates in this key.
    """
    return hashlib.sha256(repr(tuple(recording)).encode("utf-8")).hexdigest()


def cff_full_annotation_signature(style_group: str, body_signatures, tone_signature: str = "", neutral_signature: str = "") -> str:
    """Exact signature for one complete embedded CFF annotation.

    This deliberately includes the style group and every body/tone component.
    It is suitable only for exact verified replay; no fuzzy similarity is used.
    """
    payload = {
        "style": str(style_group or ""),
        "body": ";".join(str(x or "") for x in (body_signatures or [])),
        "tone": str(tone_signature or ""),
        "neutral": str(neutral_signature or ""),
    }
    raw = repr(tuple(sorted(payload.items()))).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _classify_tone(tone_contours, neutral_contours):
    if neutral_contours:
        return "˙", "中性輕聲點"
    if not tone_contours:
        return "", "一聲無調號"
    corr = _tone_correlation(tone_contours)
    if corr is None:
        return "?", "聲調輪廓不足"
    # v4.5 fail-closed tone gate.  The verified families occupy three clearly
    # separated correlation bands; values in the gaps are never guessed.
    if 0.85 <= corr <= 0.98:
        return "ˊ", f"二聲安全輪廓 corr={corr:.3f}"
    if -0.98 <= corr <= -0.85:
        return "ˋ", f"四聲安全輪廓 corr={corr:.3f}"
    if -0.20 <= corr <= 0.60:
        return "ˇ", f"三聲安全輪廓 corr={corr:.3f}"
    return "?", f"聲調輪廓落在安全區外 corr={corr:.3f}"


class CFFZhuyinInspector:
    def __init__(
        self,
        data: bytes,
        font_name: str,
        symbol_map,
        verified_glyph_fingerprints=None,
        *,
        global_snapshot: GlobalExactGlyphSnapshot | None = None,
        quarantined_glyph_identities=None,
    ):
        cff = CFFFontSet()
        cff.decompile(BytesIO(data), None)
        top_name = list(cff.keys())[0]
        self.top = cff[top_name]
        self.font_name = str(font_name or "")
        self.style_group = cff_style_group(self.font_name)
        self.symbol_map = symbol_map
        self.verified_glyph_fingerprints = verified_glyph_fingerprints or {}
        self.global_snapshot = global_snapshot
        self.quarantined_glyph_identities = set(quarantined_glyph_identities or ())

    def _recording(self, glyph_id: int):
        name = f"cid{int(glyph_id):05d}"
        pen = RecordingPen()
        self.top.CharStrings[name].draw(pen)
        return pen.value

    def decode(self, glyph_id: int):
        """Decode only evidence physically present in the CFF glyph.

        Returns `detected=False` for glyphs with no Bopomofo annotation column
        (punctuation / Latin / unannotated glyphs).  Unknown visual symbols stay
        unresolved and are never inferred from the Han character.
        """
        try:
            recording = self._recording(int(glyph_id))
        except Exception as exc:
            return {
                "detected": False,
                "reading": "",
                "status": "CFF字形不可讀",
                "method": f"CFF charstring 讀取失敗：{exc}",
            }

        body_contours, tone_contours = [], []
        for contour in _split_contours(recording):
            bb = _bounds(contour)
            if not bb:
                continue
            x0, y0, x1, y1 = bb
            if x0 < ANNOTATION_X_MIN:
                continue
            if x0 >= TONE_X_MIN:
                tone_contours.append(contour)
            else:
                body_contours.append(contour)

        # A number of Bopomofo symbols (notably ㄖ) contain a small detached
        # contour.  Size alone therefore cannot mean neutral tone.  A genuine
        # neutral dot in this corpus is a small contour *above* the complete
        # Bopomofo stack, and never coexists with a far-right tone mark.
        tiny, normal = [], []
        for contour in body_contours:
            bb = _bounds(contour)
            if not bb:
                continue
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            (tiny if (w <= NEUTRAL_MAX_W and h <= NEUTRAL_MAX_H) else normal).append(contour)
        neutral_contours = []
        # The verified CFF symbol-signature table was intentionally built from
        # the non-tiny body contours.  Small detached symbol details (e.g. the
        # inner stroke of ㄖ) are therefore ignored for the signature but must
        # not be mistaken for neutral tone.
        base_contours = list(normal)
        if not tone_contours:
            max_normal_y = max((_bounds(c)[3] for c in normal if _bounds(c)), default=None)
            for contour in tiny:
                bb = _bounds(contour)
                if max_normal_y is not None and bb and bb[1] > max_normal_y + 8:
                    neutral_contours.append(contour)

        groups = _group_by_y(base_contours)
        if not groups:
            return {
                "detected": False,
                "reading": "",
                "status": "無CFF注音欄",
                "method": "CFF整字未發現右側注音符號",
                "style_group": self.style_group,
            }

        signatures, symbols, unknown = [], [], []
        for group in groups:
            sig = _signature(group)
            signatures.append(sig)
            rec = self.symbol_map.get((self.style_group, sig)) if self.style_group else None
            if rec:
                symbols.append(rec["symbol"])
            else:
                symbols.append("")
                unknown.append(sig)

        tone_signature = _signature(tone_contours) if tone_contours else ""
        neutral_signature = _signature(neutral_contours) if neutral_contours else ""
        full_signature = cff_full_annotation_signature(self.style_group, signatures, tone_signature, neutral_signature)
        glyph_sha256 = cff_glyph_outline_sha256(recording)
        tone, tone_evidence = _classify_tone(tone_contours, neutral_contours)
        base = "".join(symbols)
        verified = self.verified_glyph_fingerprints.get((self.style_group, glyph_sha256))
        exact_reading = str(verified.get("bopomofo") or "") if verified else ""
        exact_sources: tuple[str, ...] = ("PROJECT_VERIFIED_EXACT",) if exact_reading else ()
        exact_conflict = (self.style_group, glyph_sha256) in self.quarantined_glyph_identities
        exact_conflicting_readings: tuple[str, ...] = ()
        global_effective_state = ""
        if self.global_snapshot is not None and self.style_group:
            identity = canonical_global_exact_identity(
                CFF_GLYPH_SHA256,
                self.style_group,
                glyph_sha256,
                GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
            )
            resolution = resolve_exact_glyph_reuse(
                self.global_snapshot,
                identity,
                higher_priority_sources=(
                    (("PROJECT_VERIFIED_EXACT", exact_reading),)
                    if exact_reading
                    else ()
                ),
                exact_identity_quarantined=exact_conflict,
            )
            exact_reading = resolution.reading
            exact_sources = resolution.sources
            exact_conflict = resolution.conflict
            exact_conflicting_readings = resolution.conflicting_readings
            global_effective_state = resolution.global_effective_state
        elif exact_conflict:
            exact_reading = ""
            exact_sources = ()

        exact_source_label = ""
        if exact_reading:
            if exact_sources == ("PROJECT_VERIFIED_EXACT", "GLOBAL_VERIFIED_EXACT"):
                exact_source_label = "Project exact + Global exact agreement"
            elif "GLOBAL_VERIFIED_EXACT" in exact_sources:
                exact_source_label = "Global exact CFF（樣式群組 + 完整字形 SHA-256）"
            else:
                exact_source_label = "使用者雙例驗證 exact CFF 整字字形 SHA-256"
            reading = exact_reading
            status = "已解碼"
            method = exact_source_label
            tone_evidence = f"{tone_evidence}；exact CFF full-glyph SHA-256 verified"
        elif unknown or not base or tone == "?":
            reading = ""
            status = "CFF符號待建立對照"
            method = "CFF整字右側注音輪廓直接解碼"
        else:
            reading = ("˙" + base) if tone == "˙" else (base + tone)
            status = "已解碼"
            method = "CFF整字右側注音輪廓直接解碼"
        return {
            "detected": True,
            "reading": reading,
            "status": status,
            "method": method,
            "style_group": self.style_group,
            "signatures": signatures,
            "symbols": symbols,
            "unknown_signatures": unknown,
            "tone": tone,
            "tone_evidence": tone_evidence,
            "tone_signature": tone_signature,
            "neutral_signature": neutral_signature,
            "full_signature": full_signature,
            "glyph_sha256": glyph_sha256,
            "neutral_count": len(neutral_contours),
            "exact_source_label": exact_source_label,
            "exact_reading": exact_reading,
            "exact_reuse_sources": exact_sources,
            "exact_conflict": exact_conflict,
            "exact_conflicting_readings": exact_conflicting_readings,
            "global_effective_state": global_effective_state,
        }
