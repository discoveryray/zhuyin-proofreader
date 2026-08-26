from __future__ import annotations

"""TrueType Bopomofo annotation shape decomposition.

This module deliberately works from glyph outlines only.  It never receives
Chinese-character text, dictionary readings, or sentence context when deciding
an unknown pronunciation.  Known mapped glyphs are used only as training
examples for reusable Bopomofo-symbol and tone-mark outline signatures.

Safety model:
- exact contour signatures (translation invariant; no fuzzy nearest-neighbour),
- per-font models by default,
- symbol/tone template purity gates,
- deterministic key-level out-of-fold validation before a font is allowed to
  decode unknown glyphs,
- unknown or ambiguous outlines remain unknown.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import itertools
from typing import Iterable


TONE_SUFFIX = {"2": "ˊ", "3": "ˇ", "4": "ˋ"}


def split_reading(reading: str):
    s = str(reading or "")
    if s.startswith("˙"):
        return s[1:], "neutral"
    if s.endswith("ˊ"):
        return s[:-1], "2"
    if s.endswith("ˋ"):
        return s[:-1], "4"
    if s.endswith("ˇ"):
        return s[:-1], "3"
    return s, "1"


def compose_reading(base: str, tone: str):
    if tone == "neutral":
        return "˙" + base
    return base + TONE_SUFFIX.get(tone, "")


def contour_bbox(contour):
    xs = [p[0] for p in contour]
    ys = [p[1] for p in contour]
    return min(xs), min(ys), max(xs), max(ys)


def _canonical_cycle(seq):
    if not seq:
        return ()
    candidates = []
    for direction in (list(seq), list(reversed(seq))):
        minimum = min(direction)
        for idx, point in enumerate(direction):
            if point == minimum:
                candidates.append(tuple(direction[idx:] + direction[:idx]))
    return min(candidates)


def contour_group_signature(contours):
    """Exact, translation-invariant outline signature for one symbol group."""
    if not contours:
        return ""
    xs = [p[0] for contour in contours for p in contour]
    ys = [p[1] for contour in contours for p in contour]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    normalized = []
    for contour in contours:
        seq = [(x - x0, y - y0, int(on_curve)) for x, y, on_curve in contour]
        normalized.append(_canonical_cycle(seq))
    payload = (x1 - x0, y1 - y0, tuple(sorted(normalized)))
    return hashlib.sha1(repr(payload).encode("utf-8")).hexdigest()


def sort_contours_top_to_bottom(contours):
    return sorted(
        contours,
        key=lambda c: (
            -(contour_bbox(c)[1] + contour_bbox(c)[3]) / 2,
            (contour_bbox(c)[0] + contour_bbox(c)[2]) / 2,
        ),
    )


def strip_known_tone(contours, tone: str):
    """Remove the tone contour using the known reading only during training."""
    contours = list(contours)
    if tone == "1" or not contours:
        return contours, None
    boxes = [contour_bbox(c) for c in contours]
    if tone == "neutral":
        idx = max(
            range(len(contours)),
            key=lambda j: (
                (boxes[j][1] + boxes[j][3]) / 2,
                -((boxes[j][2] - boxes[j][0]) * (boxes[j][3] - boxes[j][1])),
            ),
        )
    else:
        # In these vertical textbook annotation fonts, 2/3/4 tone marks are
        # isolated on the right side of the Bopomofo column.
        idx = max(
            range(len(contours)),
            key=lambda j: ((boxes[j][0] + boxes[j][2]) / 2, boxes[j][0]),
        )
    return contours[:idx] + contours[idx + 1 :], contours[idx]


def split_unknown_tone(contours):
    """Find a tone contour without using a pronunciation label.

    Returns (base_contours, tone_contour, hint) where hint is one of:
    ``right`` (2/3/4 tone candidate), ``neutral`` or ``none`` (first tone).
    Ambiguous geometry is kept as ``none`` rather than guessed.
    """
    contours = list(contours)
    if len(contours) <= 1:
        return contours, None, "none"

    boxes = [contour_bbox(c) for c in contours]

    # 2/3/4 tones: a clearly horizontally separated right-hand contour.
    idx = max(range(len(contours)), key=lambda j: (boxes[j][0] + boxes[j][2]) / 2)
    other_boxes = [boxes[j] for j in range(len(contours)) if j != idx]
    if other_boxes:
        max_other_x = max(b[2] for b in other_boxes)
        if boxes[idx][0] >= max_other_x + 8:
            return contours[:idx] + contours[idx + 1 :], contours[idx], "right"

    # Neutral tone: a small, roughly square contour clearly above the Bopomofo
    # column.  The aspect-ratio gate prevents the horizontal stroke of ㄧ from
    # being mistaken for the neutral-tone dot.
    idx = max(range(len(contours)), key=lambda j: (boxes[j][1] + boxes[j][3]) / 2)
    other_boxes = [boxes[j] for j in range(len(contours)) if j != idx]
    if other_boxes:
        max_other_y = max(b[3] for b in other_boxes)
        area = (boxes[idx][2] - boxes[idx][0]) * (boxes[idx][3] - boxes[idx][1])
        other_areas = sorted((b[2] - b[0]) * (b[3] - b[1]) for b in other_boxes)
        median_area = other_areas[len(other_areas) // 2]
        width = max(1, boxes[idx][2] - boxes[idx][0])
        height = max(1, boxes[idx][3] - boxes[idx][1])
        aspect = width / height
        if (
            boxes[idx][1] >= max_other_y + 15
            and area < median_area * 0.55
            and 0.55 <= aspect <= 1.8
        ):
            return contours[:idx] + contours[idx + 1 :], contours[idx], "neutral"

    return contours, None, "none"


def contiguous_partitions(sequence, parts: int):
    count = len(sequence)
    if parts < 1 or parts > count:
        return
    for cuts in itertools.combinations(range(1, count), parts - 1):
        previous = 0
        result = []
        for cut in cuts + (count,):
            result.append(sequence[previous:cut])
            previous = cut
        yield result


@dataclass
class ShapeEvent:
    key: object
    contours: list
    reading: str


@dataclass
class ValidationResult:
    font: str
    unique_keys: int = 0
    predicted: int = 0
    correct: int = 0
    precision: float = 0.0
    coverage: float = 0.0
    ready: bool = False
    reasons: Counter = field(default_factory=Counter)
    oof_predictions: dict = field(default_factory=dict)


class TTFZhuyinShapeModel:
    def __init__(self, min_template_purity: float = 0.995, min_template_support: int = 2):
        self.min_template_purity = float(min_template_purity)
        self.min_template_support = int(min_template_support)
        self.symbol_templates = defaultdict(Counter)
        self.tone_templates = defaultdict(Counter)
        self.events = []
        self.unresolved_events = []

    def add_event(self, contours, reading: str):
        base, tone = split_reading(reading)
        if not base or not contours:
            return
        base_contours, tone_contour = strip_known_tone(contours, tone)
        base_contours = sort_contours_top_to_bottom(base_contours)
        if tone_contour is not None:
            self.tone_templates[contour_group_signature([tone_contour])][tone] += 1
        self.events.append((base_contours, list(base)))

    @staticmethod
    def _top(counter: Counter):
        if not counter:
            return None, 0, 0.0
        label, count = counter.most_common(1)[0]
        total = sum(counter.values())
        return label, count, count / total if total else 0.0

    def learn(self, max_iterations: int = 8):
        unresolved = []
        for contours, symbols in self.events:
            if len(symbols) == 1:
                self.symbol_templates[contour_group_signature(contours)][symbols[0]] += 1
            elif len(contours) == len(symbols):
                for contour, symbol in zip(contours, symbols):
                    self.symbol_templates[contour_group_signature([contour])][symbol] += 1
            else:
                unresolved.append((contours, symbols))

        # Resolve multi-contour Bopomofo symbols only when there is exactly one
        # partition compatible with the templates already learned.  This avoids
        # choosing the partition that merely scores best.
        for _ in range(max_iterations):
            added = 0
            still_unresolved = []
            for contours, symbols in unresolved:
                possible = []
                for partition in contiguous_partitions(contours, len(symbols)):
                    rejected = False
                    for group, expected_symbol in zip(partition, symbols):
                        counter = self.symbol_templates.get(contour_group_signature(group))
                        if counter:
                            top, _count, purity = self._top(counter)
                            if top != expected_symbol or purity < 0.98:
                                rejected = True
                                break
                    if not rejected:
                        possible.append(partition)
                if len(possible) == 1:
                    for group, symbol in zip(possible[0], symbols):
                        self.symbol_templates[contour_group_signature(group)][symbol] += 1
                    added += 1
                else:
                    still_unresolved.append((contours, symbols))
            unresolved = still_unresolved
            if not added:
                break
        self.unresolved_events = unresolved
        return self

    def decode(self, contours):
        if not contours:
            return {"reading": "", "reason": "no_simple_contours"}

        base_contours, tone_contour, tone_hint = split_unknown_tone(contours)
        base_contours = sort_contours_top_to_bottom(base_contours)

        if tone_hint == "neutral":
            tone = "neutral"
            tone_support = None
            tone_purity = 1.0
        elif tone_hint == "none":
            tone = "1"
            tone_support = None
            tone_purity = 1.0
        else:
            signature = contour_group_signature([tone_contour])
            counter = self.tone_templates.get(signature)
            if not counter:
                return {"reading": "", "reason": "unknown_tone", "tone_signature": signature}
            tone, tone_support, tone_purity = self._top(counter)
            if (
                tone_purity < self.min_template_purity
                or tone_support < self.min_template_support
            ):
                return {
                    "reading": "",
                    "reason": "ambiguous_tone",
                    "tone": tone,
                    "tone_support": tone_support,
                    "tone_purity": tone_purity,
                }

        candidates = []
        max_symbols = min(3, len(base_contours))
        for symbol_count in range(1, max_symbols + 1):
            for partition in contiguous_partitions(base_contours, symbol_count):
                letters = []
                template_stats = []
                valid = True
                for group in partition:
                    signature = contour_group_signature(group)
                    counter = self.symbol_templates.get(signature)
                    if not counter:
                        valid = False
                        break
                    symbol, support, purity = self._top(counter)
                    if (
                        purity < self.min_template_purity
                        or support < self.min_template_support
                    ):
                        valid = False
                        break
                    letters.append(symbol)
                    template_stats.append({
                        "symbol": symbol,
                        "signature": signature,
                        "support": support,
                        "purity": purity,
                    })
                if valid:
                    candidates.append((compose_reading("".join(letters), tone), template_stats))

        unique_readings = sorted({reading for reading, _stats in candidates})
        if len(unique_readings) != 1:
            return {
                "reading": "",
                "reason": "multiple_readings" if unique_readings else "unknown_symbol",
                "candidates": unique_readings,
            }

        reading = unique_readings[0]
        matching_stats = [stats for candidate, stats in candidates if candidate == reading]
        best_stats = max(
            matching_stats,
            key=lambda stats: min((s["support"] for s in stats), default=0),
        )
        return {
            "reading": reading,
            "reason": "ok",
            "symbols": best_stats,
            "tone": tone,
            "tone_support": tone_support,
            "tone_purity": tone_purity,
            "min_symbol_support": min((s["support"] for s in best_stats), default=0),
            "min_symbol_purity": min((s["purity"] for s in best_stats), default=0.0),
        }


def train_model(events: Iterable[ShapeEvent], min_template_purity=0.995, min_template_support=2):
    model = TTFZhuyinShapeModel(min_template_purity, min_template_support)
    for event in events:
        model.add_event(event.contours, event.reading)
    model.learn()
    return model


def cross_validate_font(
    font: str,
    events: Iterable[ShapeEvent],
    folds: int = 5,
    min_unique_keys: int = 200,
    min_predictions: int = 100,
    min_precision: float = 0.995,
    min_coverage: float = 0.30,
    min_template_purity: float = 0.995,
    min_template_support: int = 2,
):
    """Deterministic key-level OOF validation and readiness decision."""
    by_key = defaultdict(list)
    for event in events:
        by_key[event.key].append(event)
    keys = sorted(by_key, key=lambda x: repr(x))
    result = ValidationResult(font=font, unique_keys=len(keys))
    if not keys or len(keys) < min_unique_keys:
        result.reasons["insufficient_unique_keys"] += len(keys)
        return result

    for fold in range(max(2, int(folds))):
        test_keys = {key for idx, key in enumerate(keys) if idx % folds == fold}
        training = [
            event
            for key, group in by_key.items()
            if key not in test_keys
            for event in group
        ]
        model = train_model(training, min_template_purity, min_template_support)
        for key in test_keys:
            # All outline variants for a stable component key must agree.  If a
            # subset collision yields different predictions, do not emit OOF.
            emitted = []
            true_readings = {event.reading for event in by_key[key]}
            for event in by_key[key]:
                decoded = model.decode(event.contours)
                result.reasons[decoded.get("reason", "unknown")] += 1
                if decoded.get("reading"):
                    emitted.append(decoded["reading"])
            if not emitted or len(set(emitted)) != 1 or len(true_readings) != 1:
                continue
            predicted = emitted[0]
            truth = next(iter(true_readings))
            result.predicted += 1
            if predicted == truth:
                result.correct += 1
            result.oof_predictions[key] = predicted

    result.precision = result.correct / result.predicted if result.predicted else 0.0
    result.coverage = result.predicted / result.unique_keys if result.unique_keys else 0.0
    result.ready = (
        result.unique_keys >= min_unique_keys
        and result.predicted >= min_predictions
        and result.precision >= min_precision
        and result.coverage >= min_coverage
    )
    return result
