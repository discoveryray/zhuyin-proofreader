"""Read-only PDF text context for the review screen, never ledger evidence."""
from __future__ import annotations

import fitz


_STOP = "。！？!?；;"


def _nearby_lines(raw):
    lines = []
    for block_index, block in enumerate(raw.get("blocks", [])):
        for line in block.get("lines", []):
            chars = [char for span in line.get("spans", []) for char in span.get("chars", [])]
            if chars:
                lines.append((block_index, fitz.Rect(line["bbox"]), chars))
    return lines


def _continuous(a, b):
    """Allow an actual printed wrap, including one split across PDF blocks."""
    _, previous, _ = a
    _, following, _ = b
    height = max(previous.height, following.height)
    gap = following.y0 - previous.y1
    return (0 <= gap <= min(10, height * 0.65)
            and abs(following.x0 - previous.x0) <= min(12, height * 0.75)
            and following.x0 < previous.x1)


def pdf_sentence_at_target(raw, target_rect, target_char):
    """Return a geometrically continuous sentence containing the exact target.

    A different column, paragraph gap, or ambiguous character box produces no
    extension. The result is a display hint from PDF text, not a pronunciation.
    """
    try:
        target = fitz.Rect(target_rect)
        if target.is_empty or len(target_char) != 1:
            return None
        lines = _nearby_lines(raw)
        hits = []
        for index, (_, _bounds, chars) in enumerate(lines):
            for char_index, char in enumerate(chars):
                box = fitz.Rect(char["bbox"])
                intersection = box & target
                if (char.get("c") == target_char and not intersection.is_empty
                        and intersection.get_area() >= box.get_area() * 0.3):
                    hits.append((index, char_index))
        if len(hits) != 1:
            return None
        index, char_index = hits[0]
        base = lines[index]
        # Only lines in the same narrow text column and regular line spacing.
        before = "".join(char["c"] for char in base[2][:char_index])
        after = "".join(char["c"] for char in base[2][char_index:])
        for candidate in sorted((item for j, item in enumerate(lines) if j != index),
                                key=lambda item: item[1].y1, reverse=True):
            if any(mark in before for mark in _STOP):
                break
            if _continuous(candidate, base):
                before = "".join(char["c"] for char in candidate[2]) + before
                base = candidate
        base = lines[index]
        for candidate in sorted((item for j, item in enumerate(lines) if j != index),
                                key=lambda item: item[1].y0):
            if any(mark in after for mark in _STOP):
                break
            if _continuous(base, candidate):
                after += "".join(char["c"] for char in candidate[2])
                base = candidate
        preceding_boundary = max((before.rfind(mark) for mark in _STOP), default=-1)
        before = before[preceding_boundary + 1:]
        following_boundary = min((after.index(mark) for mark in _STOP if mark in after), default=-1)
        if following_boundary >= 0:
            after = after[:following_boundary + 1]
        result = (before + after).strip()
        return result if target_char in result and 1 < len(result) <= 500 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
