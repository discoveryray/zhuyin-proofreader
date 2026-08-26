from __future__ import annotations

"""Exact Bopomofo symbol recombination for TrueType annotation components.

This layer is deliberately geometry-only at runtime.  It splits an annotation
component into vertically stacked Bopomofo symbol groups plus an optional tone
mark, then resolves every group by an exact, translation-invariant outline
signature from a pre-validated static template table.

Safety constraints:
- no Chinese-character text, dictionary, or sentence context is accepted;
- no fuzzy distance or nearest-neighbour lookup;
- templates are conflict-free and require either >=2 distinct verified source keys
  or an explicitly admitted direct isolated-symbol visual verification;
- every target xref variant must independently produce the same full reading;
- unknown / ambiguous geometry remains unknown.
"""

from dataclasses import dataclass
from pathlib import Path
import csv

from ttf_zhuyin_shape_decoder import contour_bbox, contour_group_signature, compose_reading


def cluster_vertical_symbol_groups(contours):
    """Group contours that occupy the same vertical symbol band.

    The thresholds are the exact geometry rule validated in the v3.8 research
    experiments across the 3-up and 3-down textbooks.  The function only uses
    contour coordinates; it never sees the underlying Han character.
    """
    items = []
    for contour in contours or []:
        bbox = contour_bbox(contour)
        items.append((bbox, contour))
    items.sort(key=lambda item: -((item[0][1] + item[0][3]) / 2))
    groups = []
    for bbox, contour in items:
        cy = (bbox[1] + bbox[3]) / 2
        height = bbox[3] - bbox[1]
        candidates = []
        for index, group in enumerate(groups):
            gb = group["bbox"]
            gcy = (gb[1] + gb[3]) / 2
            gheight = gb[3] - gb[1]
            overlap = max(0, min(bbox[3], gb[3]) - max(bbox[1], gb[1]))
            if (
                overlap >= 0.45 * min(max(height, 1), max(gheight, 1))
                or abs(cy - gcy) <= 0.22 * max(height, gheight, 1)
            ):
                candidates.append((overlap / max(1, min(height, gheight)), -abs(cy - gcy), index))
        if not candidates:
            groups.append({"bbox": bbox, "contours": [contour]})
            continue
        _overlap_score, _distance_score, index = max(candidates)
        group = groups[index]
        gb = group["bbox"]
        group["contours"].append(contour)
        group["bbox"] = (
            min(gb[0], bbox[0]), min(gb[1], bbox[1]),
            max(gb[2], bbox[2]), max(gb[3], bbox[3]),
        )
    groups.sort(key=lambda group: -((group["bbox"][1] + group["bbox"][3]) / 2))
    return groups


def split_symbol_geometry(contours):
    """Return (vertical_groups, right_tone_contour) without a reading label.

    In the validated textbook TTF annotation fonts, 2/3/4 tone marks occupy a
    distinct right-side contour band whose centre x is >330 font units.  A
    right-tone mark is removed only when *exactly one* contour meets that gate;
    otherwise the target is left intact and will normally fail closed.
    """
    contours = list(contours or [])
    if not contours:
        return [], None
    boxes = [contour_bbox(c) for c in contours]
    right = [i for i, b in enumerate(boxes) if (b[0] + b[2]) / 2 > 330]
    tone_contour = None
    if len(right) == 1:
        index = right[0]
        tone_contour = contours[index]
        base = [c for j, c in enumerate(contours) if j != index]
    else:
        base = contours
    return cluster_vertical_symbol_groups(base), tone_contour


@dataclass(frozen=True)
class SymbolTemplate:
    template_type: str
    signature: str
    label: str
    support_keys: int
    support_fonts: int
    source_examples: str = ""
    validation: str = ""
    notes: str = ""


class ExactSymbolRecombinationModel:
    def __init__(self, templates):
        self.symbols = {}
        self.tones = {}
        self.neutral = {}
        self.templates = list(templates)
        for t in self.templates:
            if t.template_type == "symbol":
                self.symbols[t.signature] = t
            elif t.template_type == "tone":
                self.tones[t.signature] = t
            elif t.template_type == "neutral":
                self.neutral[t.signature] = t

    @classmethod
    def load_csv(cls, path: Path):
        path = Path(path)
        templates = []
        if not path.exists():
            return cls([])
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                typ = (row.get("template_type") or "").strip().lower()
                sig = (row.get("outline_signature") or "").strip().lower()
                label = (row.get("label") or "").strip()
                try:
                    support_keys = int(row.get("support_keys") or 0)
                    support_fonts = int(row.get("support_fonts") or 0)
                except Exception:
                    continue
                if typ not in {"symbol", "tone", "neutral"} or not sig or not label:
                    continue
                # Runtime table is fail-closed even if the CSV is accidentally
                # edited. Automated research templates require >=2 distinct
                # verified full-glyph source keys. A symbol-only exception is
                # allowed only when the CSV explicitly records a direct isolated
                # visual verification. This exception is still exact-signature
                # only and never uses Han text, context, dictionary, fuzzy
                # similarity, or nearest-neighbour inference.
                validation = (row.get("validation") or "").strip()
                direct_visual = (
                    typ in {"symbol", "tone", "neutral"} and
                    validation.lower().startswith(("direct_isolated_visual_", "direct_whole_glyph_visual_truth_"))
                )
                if support_keys < 2 and not direct_visual:
                    continue
                templates.append(SymbolTemplate(
                    typ, sig, label, support_keys, support_fonts,
                    (row.get("source_examples") or "").strip(),
                    (row.get("validation") or "").strip(),
                    (row.get("notes") or "").strip(),
                ))
        return cls(templates)

    def decode(self, contours):
        groups, tone_contour = split_symbol_geometry(contours)
        if not groups:
            return {"reading": "", "reason": "no_symbol_groups"}

        evidence = []
        tone = "1"

        # Neutral tone is encoded as a distinct top vertical group.  It is
        # removed only on an exact signature hit in the verified neutral table.
        first_sig = contour_group_signature(groups[0]["contours"])
        neutral_template = self.neutral.get(first_sig)
        if neutral_template is not None:
            tone = "neutral"
            evidence.append({
                "type": "neutral", "signature": first_sig,
                "label": "neutral", "support_keys": neutral_template.support_keys,
                "support_fonts": neutral_template.support_fonts,
            })
            groups = groups[1:]
            if not groups:
                return {"reading": "", "reason": "neutral_without_base", "evidence": evidence}

        if tone_contour is not None:
            if tone == "neutral":
                return {"reading": "", "reason": "neutral_plus_right_tone", "evidence": evidence}
            tone_sig = contour_group_signature([tone_contour])
            tone_template = self.tones.get(tone_sig)
            if tone_template is None:
                return {"reading": "", "reason": "unknown_tone_signature", "tone_signature": tone_sig, "evidence": evidence}
            tone = tone_template.label
            evidence.append({
                "type": "tone", "signature": tone_sig,
                "label": tone, "support_keys": tone_template.support_keys,
                "support_fonts": tone_template.support_fonts,
            })

        # Fast path: each vertical band is already one complete Bopomofo symbol.
        if len(groups) <= 3:
            symbols = []
            direct_evidence = list(evidence)
            direct_ok = True
            missing_sig = ""
            for group in groups:
                sig = contour_group_signature(group["contours"])
                template = self.symbols.get(sig)
                if template is None:
                    direct_ok = False
                    missing_sig = sig
                    break
                symbols.append(template.label)
                direct_evidence.append({
                    "type": "symbol", "signature": sig,
                    "label": template.label, "support_keys": template.support_keys,
                    "support_fonts": template.support_fonts,
                })
            if direct_ok:
                base = "".join(symbols)
                if not base:
                    return {"reading": "", "reason": "empty_base", "evidence": direct_evidence}
                return {
                    "reading": compose_reading(base, tone),
                    "reason": "ok",
                    "base": base,
                    "tone": tone,
                    "evidence": direct_evidence,
                    "geometry_mode": "direct_bands",
                    "min_support_keys": min((e.get("support_keys", 0) for e in direct_evidence), default=0),
                    "min_support_fonts": min((e.get("support_fonts", 0) for e in direct_evidence), default=0),
                }
        else:
            missing_sig = ""

        # Safe fallback: a single Bopomofo symbol can itself span multiple
        # vertical bands.  Enumerate all contiguous partitions and accept only
        # a unique exact-signature reading.
        partitioned = self._decode_partitioned_groups(groups, tone, evidence)
        if partitioned.get("reading"):
            return partitioned
        if len(groups) > 3 and partitioned.get("reason") == "partition_unknown_symbol":
            partitioned["legacy_reason"] = "too_many_symbol_groups"
        elif missing_sig:
            partitioned["symbol_signature"] = missing_sig
            partitioned["legacy_reason"] = "unknown_symbol_signature"
        return partitioned

    def _decode_partitioned_groups(self, groups, tone, evidence):
        """Fail-closed fallback for Bopomofo symbols split across vertical bands.

        Some annotation fonts split a single multi-stroke Bopomofo symbol into
        two or more vertically adjacent geometry bands.  The normal decoder
        intentionally treats each band independently; when that fails, this
        fallback enumerates *all* contiguous partitions into at most three
        Bopomofo symbols and accepts a result only when exactly one full reading
        can be composed from exact verified symbol signatures.

        Runtime evidence remains geometry-only: no Han text, dictionary,
        sentence context, fuzzy similarity or nearest-neighbour lookup.
        """
        if not groups or len(groups) > 8:
            return {"reading": "", "reason": "partition_bad_group_count", "group_count": len(groups), "evidence": list(evidence)}

        # Local import avoids changing the public API of the shape decoder.
        from ttf_zhuyin_shape_decoder import contiguous_partitions

        candidates = []
        max_symbols = min(3, len(groups))
        for symbol_count in range(1, max_symbols + 1):
            for partition in contiguous_partitions(groups, symbol_count):
                symbols = []
                part_evidence = []
                valid = True
                for part in partition:
                    contours = []
                    for band in part:
                        contours.extend(band["contours"])
                    sig = contour_group_signature(contours)
                    template = self.symbols.get(sig)
                    if template is None:
                        valid = False
                        break
                    symbols.append(template.label)
                    part_evidence.append({
                        "type": "symbol", "signature": sig,
                        "label": template.label, "support_keys": template.support_keys,
                        "support_fonts": template.support_fonts,
                        "partition_bands": len(part),
                    })
                if valid:
                    base = "".join(symbols)
                    candidates.append({
                        "reading": compose_reading(base, tone),
                        "base": base,
                        "evidence": list(evidence) + part_evidence,
                        "partition": [len(part) for part in partition],
                    })

        readings = sorted({c["reading"] for c in candidates})
        if len(readings) != 1:
            return {
                "reading": "",
                "reason": "partition_multiple_readings" if readings else "partition_unknown_symbol",
                "candidates": readings,
                "group_count": len(groups),
                "evidence": list(evidence),
                "geometry_mode": "partitioned_bands",
            }
        reading = readings[0]
        matching = [c for c in candidates if c["reading"] == reading]
        # Evidence is exact in every candidate. Prefer fewer composed bands only
        # as a deterministic audit representation; it does not choose between
        # different readings (those are rejected above).
        chosen = min(matching, key=lambda c: (sum(c["partition"]), c["partition"]))
        ev = chosen["evidence"]
        return {
            "reading": reading,
            "reason": "ok",
            "base": chosen["base"],
            "tone": tone,
            "evidence": ev,
            "geometry_mode": "partitioned_bands",
            "partition_bands": chosen["partition"],
            "min_support_keys": min((e.get("support_keys", 0) for e in ev), default=0),
            "min_support_fonts": min((e.get("support_fonts", 0) for e in ev), default=0),
        }

    def decode_nested_composite(self, inspector, glyph_id):
        """Decode a pronunciation component that is itself a composite glyph.

        Some Bpmf* textbook fonts store the visible annotation as a composite of
        standalone Bopomofo child glyphs.  This path uses only child component
        geometry and exact outline signatures.  It rejects transformed children,
        ambiguous right-side roles, unknown signatures, and inconsistent layout.
        No Han text, dictionary, sentence context, fuzzy metric, or nearest-neighbour
        lookup is available here.
        """
        try:
            components = inspector.components(int(glyph_id))
        except Exception:
            components = []
        if not components:
            return {"reading": "", "reason": "no_nested_components", "geometry_mode": "nested_components"}

        items = []
        for comp in components:
            flags = int(comp.get("flags") or 0)
            # Scale / 2x2 transform would change the exact child geometry; fail closed.
            if flags & (0x0008 | 0x0040 | 0x0080):
                return {"reading": "", "reason": "nested_transform_not_supported", "geometry_mode": "nested_components"}
            x = comp.get("x"); y = comp.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                return {"reading": "", "reason": "nested_missing_xy", "geometry_mode": "nested_components"}
            try:
                contours = inspector.simple_contours(int(comp.get("gid")))
            except Exception:
                contours = []
            if not contours:
                return {"reading": "", "reason": "nested_child_not_simple", "geometry_mode": "nested_components"}
            sig = contour_group_signature(contours)
            items.append({"x": x, "y": y, "sig": sig, "gid": int(comp.get("gid"))})

        min_x = min(item["x"] for item in items)
        right = [i for i, item in enumerate(items) if item["x"] - min_x >= 180]
        if len(right) > 1:
            return {"reading": "", "reason": "nested_multiple_right_children", "geometry_mode": "nested_components"}

        tone = "1"
        evidence = []
        base = list(range(len(items)))
        if len(right) == 1:
            i = right[0]
            t = self.tones.get(items[i]["sig"])
            if t is None:
                return {"reading": "", "reason": "nested_unknown_tone_signature", "tone_signature": items[i]["sig"], "geometry_mode": "nested_components"}
            tone = t.label
            evidence.append({"type": "tone", "signature": items[i]["sig"], "label": tone, "support_keys": t.support_keys, "support_fonts": t.support_fonts})
            base.remove(i)

        base.sort(key=lambda i: -items[i]["y"])
        if base:
            n = self.neutral.get(items[base[0]]["sig"])
            if n is not None:
                if tone != "1":
                    return {"reading": "", "reason": "nested_neutral_plus_right_tone", "geometry_mode": "nested_components"}
                tone = "neutral"
                evidence.append({"type": "neutral", "signature": items[base[0]]["sig"], "label": "neutral", "support_keys": n.support_keys, "support_fonts": n.support_fonts})
                base = base[1:]

        if not base or len(base) > 3:
            return {"reading": "", "reason": "nested_bad_symbol_count", "group_count": len(base), "geometry_mode": "nested_components", "evidence": evidence}
        symbols = []
        for i in base:
            t = self.symbols.get(items[i]["sig"])
            if t is None:
                return {"reading": "", "reason": "nested_unknown_symbol_signature", "symbol_signature": items[i]["sig"], "geometry_mode": "nested_components", "evidence": evidence}
            symbols.append(t.label)
            evidence.append({"type": "symbol", "signature": items[i]["sig"], "label": t.label, "support_keys": t.support_keys, "support_fonts": t.support_fonts})
        reading = compose_reading("".join(symbols), tone)
        return {
            "reading": reading, "reason": "ok", "base": "".join(symbols), "tone": tone,
            "evidence": evidence, "geometry_mode": "nested_components",
            "min_support_keys": min((e["support_keys"] for e in evidence), default=0),
            "min_support_fonts": min((e["support_fonts"] for e in evidence), default=0),
        }


def evidence_text(decoded):
    parts = []
    if decoded.get("geometry_mode"):
        parts.append(f"geometry:{decoded.get('geometry_mode')}")
    for e in decoded.get("evidence") or []:
        parts.append(
            f"{e.get('type')}:{e.get('label')}:{e.get('signature')}"
            f"[keys={e.get('support_keys')},fonts={e.get('support_fonts')}]"
        )
    return " | ".join(parts)
