from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class LexicalRule:
    rule_id: str
    priority: int
    mode: str
    target_char: str
    phrase: str
    expected_reading: str
    excluded_phrases: tuple[str, ...]
    source: str
    source_url: str
    note: str
    evidence_level: str = ""


@dataclass(frozen=True)
class RuleDecision:
    expected_reading: str
    rule_id: str
    rule_mode: str
    matched_phrase: str
    source: str
    source_url: str
    note: str
    priority: int
    evidence_level: str = ""


def load_rules(path: Path, pdf_stem: str = "") -> list[LexicalRule]:
    rules: list[LexicalRule] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            scope = (row.get("pdf_contains") or "").strip()
            if scope and scope not in str(pdf_stem or ""):
                continue
            rules.append(
                LexicalRule(
                    rule_id=(row.get("rule_id") or "").strip(),
                    priority=int(row.get("priority") or 0),
                    mode=(row.get("mode") or "").strip(),
                    target_char=(row.get("target_char") or "").strip(),
                    phrase=(row.get("phrase") or "").strip(),
                    expected_reading=(row.get("expected_reading") or "").strip(),
                    excluded_phrases=tuple(
                        x.strip() for x in (row.get("excluded_phrases") or "").split(";") if x.strip()
                    ),
                    source=(row.get("source") or "").strip(),
                    source_url=(row.get("source_url") or "").strip(),
                    note=(row.get("note") or "").strip(),
                    evidence_level=(row.get("evidence_level") or "").strip(),
                )
            )
    return rules


def _is_variation_selector(ch: str) -> bool:
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def strip_variation_selectors(text: str, target_pos: int | None = None) -> tuple[str, int | None]:
    """Remove Unicode variation selectors while preserving target position.

    The PDF text layer sometimes inserts Ideographic Variation Selectors after
    「一」.  They carry glyph-variant information but are not lexical characters,
    so phrase matching should ignore them.  This transformation uses only Unicode
    character class/ranges and never consults the PDF pronunciation.
    """
    if not text:
        return text or "", target_pos
    out = []
    new_pos = target_pos
    removed_before = 0
    for i, ch in enumerate(text):
        if _is_variation_selector(ch):
            if target_pos is not None and i < target_pos:
                removed_before += 1
            continue
        out.append(ch)
    if target_pos is not None:
        new_pos = target_pos - removed_before
    return "".join(out), new_pos


def occurrence_spans(text: str, phrase: str) -> Iterable[tuple[int, int]]:
    if not text or not phrase:
        return
    start = 0
    while True:
        i = text.find(phrase, start)
        if i < 0:
            break
        yield i, i + len(phrase)
        start = i + 1


def target_in_phrase(text: str, target_pos: int | None, phrase: str, target_char: str) -> bool:
    if target_pos is None or target_pos < 0 or not phrase or not text:
        return False
    for a, b in occurrence_spans(text, phrase):
        if a <= target_pos < b and text[target_pos] == target_char:
            # Confirm that the phrase itself contains this target occurrence.
            rel = target_pos - a
            if 0 <= rel < len(phrase) and phrase[rel] == target_char:
                return True
    return False


def resolve_rules(
    rules: list[LexicalRule],
    target_char: str,
    line_text: str,
    target_pos: int | None,
) -> tuple[RuleDecision | None, list[RuleDecision]]:
    # Ignore variation selectors inserted by some textbook fonts. They are
    # orthographic glyph selectors, not lexical characters.
    line_text, target_pos = strip_variation_selectors(line_text, target_pos)
    candidates: list[RuleDecision] = []
    for r in rules:
        if r.target_char != target_char:
            continue
        if r.mode in {"exact", "derived"}:
            # Exact/derived phrase rules may still need explicit boundary guards.
            # Example from the 3上 blind test: 「轉成功」 contains the byte
            # substring 「轉成」, but 成 belongs to 「成功」 and must not trigger
            # the lexical item 「轉成」.  Honour excluded_phrases for all lexical
            # rule modes, not only broad defaults.
            blocked = any(target_in_phrase(line_text, target_pos, p, target_char) for p in r.excluded_phrases)
            if blocked or not target_in_phrase(line_text, target_pos, r.phrase, target_char):
                continue
            candidates.append(
                RuleDecision(
                    expected_reading=r.expected_reading,
                    rule_id=r.rule_id,
                    rule_mode=r.mode,
                    matched_phrase=r.phrase,
                    source=r.source,
                    source_url=r.source_url,
                    note=r.note,
                    priority=r.priority,
                    evidence_level=r.evidence_level,
                )
            )
        elif r.mode == "skip":
            if not target_in_phrase(line_text, target_pos, r.phrase, target_char):
                continue
            candidates.append(
                RuleDecision(
                    expected_reading="",
                    rule_id=r.rule_id,
                    rule_mode=r.mode,
                    matched_phrase=r.phrase,
                    source=r.source,
                    source_url=r.source_url,
                    note=r.note,
                    priority=r.priority,
                    evidence_level=r.evidence_level,
                )
            )
        elif r.mode == "default":
            blocked = any(target_in_phrase(line_text, target_pos, p, target_char) for p in r.excluded_phrases)
            if blocked:
                continue
            candidates.append(
                RuleDecision(
                    expected_reading=r.expected_reading,
                    rule_id=r.rule_id,
                    rule_mode=r.mode,
                    matched_phrase="(預設規則)",
                    source=r.source,
                    source_url=r.source_url,
                    note=r.note,
                    priority=r.priority,
                    evidence_level=r.evidence_level,
                )
            )

    if not candidates:
        return None, []

    # Prefer higher-priority evidence, then more specific exact/longer phrases.
    candidates.sort(
        key=lambda d: (
            d.priority,
            1 if d.rule_mode == "exact" else 0,
            len(d.matched_phrase) if d.rule_mode == "exact" else 0,
        ),
        reverse=True,
    )
    best_priority = candidates[0].priority
    top = [c for c in candidates if c.priority == best_priority]
    top_exact = [c for c in top if c.rule_mode == "exact"]
    if top_exact:
        max_len = max(len(c.matched_phrase) for c in top_exact)
        top = [c for c in top_exact if len(c.matched_phrase) == max_len]

    readings = {c.expected_reading for c in top}
    if len(readings) != 1:
        return None, top
    return top[0], top



def resolve_handbook_position_rule(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve handbook rules that depend on the exact occurrence position.

    《統一用字手冊113.10.04》明列部分親屬疊詞的第二音節讀輕聲，
    以及「謝謝」第二個「謝」讀輕聲。這些詞若只用一般 phrase CSV，
    同一個字在同一詞內的兩個位置無法區分，因此在最高優先層以
    幾何重建後的可見文字位置判定。此函式不讀取 PDF 的實際注音。
    """
    if target_pos is None or target_pos < 0 or target_pos >= len(line_text or ""):
        return None
    source = "統一用字手冊113.10.04.docx"
    second_light = {
        "爸爸": ("爸", "˙ㄅㄚ"),
        "媽媽": ("媽", "˙ㄇㄚ"),
        "爺爺": ("爺", "˙ㄧㄝ"),
        "奶奶": ("奶", "˙ㄋㄞ"),
        "哥哥": ("哥", "˙ㄍㄜ"),
        "姐姐": ("姐", "˙ㄐㄧㄝ"),
        "弟弟": ("弟", "˙ㄉㄧ"),
        "妹妹": ("妹", "˙ㄇㄟ"),
        "叔叔": ("叔", "˙ㄕㄨ"),
        "伯伯": ("伯", "˙ㄅㄛ"),
        "舅舅": ("舅", "˙ㄐㄧㄡ"),
        "公公": ("公", "˙ㄍㄨㄥ"),
        "婆婆": ("婆", "˙ㄆㄛ"),
        "謝謝": ("謝", "˙ㄒㄧㄝ"),
    }
    for phrase, (ch, reading) in second_light.items():
        # Only the second occurrence is licensed by this positional rule.
        if line_text[target_pos] != ch or target_pos <= 0:
            continue
        if line_text[target_pos - 1:target_pos + 1] != phrase:
            continue
        return RuleDecision(
            expected_reading=reading,
            rule_id=f"HANDBOOK-POS-{phrase}-2",
            rule_mode="handbook_position",
            matched_phrase=phrase,
            source=source,
            source_url="",
            note="《統一用字手冊》輕聲區明列：此疊詞第二音節讀輕聲。",
            priority=10200,
            evidence_level="handbook_highest",
        )
    return None


def resolve_company_position_rule(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve company-approved occurrence-position pronunciation rules.

    These rules are independent expected evidence supplied by the current
    company policy. They never inspect the observed PDF pronunciation.
    """
    if target_pos is None or target_pos < 0 or target_pos >= len(line_text or ""):
        return None
    # User confirmed on 2026-08-19 that the company's textbook policy treats
    # the second 寶 in 寶寶 as neutral tone. This is intentionally a positional
    # rule so the first 寶 remains the lexical base reading.
    if line_text[target_pos] == "寶" and target_pos > 0 and line_text[target_pos - 1:target_pos + 1] == "寶寶":
        return RuleDecision(
            expected_reading="˙ㄅㄠ",
            rule_id="COMPANY-POS-BAOBAO-2",
            rule_mode="company_position",
            matched_phrase="寶寶（第二字）",
            source="公司現行注音規範（使用者確認 2026-08-19）",
            source_url="",
            note="公司規定『寶寶』第二個『寶』標輕聲；此規則僅依詞內位置建立 expected，不讀取 actual。",
            priority=10300,
            evidence_level="company_policy",
        )
    return None


def resolve_semantic_complete_word_rule(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve a few complete-word homographs only when visible context disambiguates sense.

    The decision uses MOE concise-dictionary definitions plus the visible text
    around the target. Ambiguous contexts remain unresolved; actual is never
    consulted.
    """
    if target_pos is None or target_pos < 0 or target_pos >= len(line_text or ""):
        return None
    ch = line_text[target_pos]
    source = "教育部《國語辭典簡編本》完整詞條義項"

    # 東西: 方位 ㄉㄨㄥ ㄒㄧ；物品 ㄉㄨㄥ ˙ㄒㄧ。 Only fire when
    # visible syntax clearly denotes things/objects.
    if ch == "西" and target_pos > 0 and line_text[target_pos - 1:target_pos + 1] == "東西":
        object_cues = ("吃東西", "哪些東西", "什麼東西", "買東西")
        direction_cues = ("東西文化", "東西走向", "東西方向", "東西橫貫")
        if any(cue in line_text for cue in object_cues):
            return RuleDecision(
                expected_reading="˙ㄒㄧ", rule_id="MOE-SENSE-DONGXI-OBJECT",
                rule_mode="semantic_word", matched_phrase="東西（物品義）", source=source, source_url="",
                note="簡編本『東西』物品義讀ㄉㄨㄥ ˙ㄒㄧ；現句由吃／哪些／什麼／買等可見語境唯一化為物品義。",
                priority=10250, evidence_level="moe_direct_context",
            )
        if any(cue in line_text for cue in direction_cues):
            return RuleDecision(
                expected_reading="ㄒㄧ", rule_id="MOE-SENSE-DONGXI-DIRECTION",
                rule_mode="semantic_word", matched_phrase="東西（方位義）", source=source, source_url="",
                note="簡編本『東西』方位／東西向義讀ㄉㄨㄥ ㄒㄧ；現句可見語境唯一化為方位義。",
                priority=10250, evidence_level="moe_direct_context",
            )

    # 好玩: '玩起來有趣' = ㄏㄠˇ ㄨㄢˊ；'喜歡玩樂' = ㄏㄠˋ ㄨㄢˊ。
    if ch == "好" and line_text[target_pos:target_pos + 2] == "好玩":
        tail = line_text[target_pos:target_pos + 8]
        if tail.startswith("好玩的") or "好玩、有趣" in line_text or "好玩而且" in line_text:
            return RuleDecision(
                expected_reading="ㄏㄠˇ", rule_id="MOE-SENSE-HAOWAN-FUN",
                rule_mode="semantic_word", matched_phrase="好玩（有趣義）", source=source, source_url="",
                note="簡編本『好玩』有趣義讀ㄏㄠˇ ㄨㄢˊ；『好玩的…』或與『有趣』並列可唯一化為此義。",
                priority=170, evidence_level="moe_direct_context",
            )

    # 看看: ㄎㄢˋ ㄎㄢˋ = observe/view/visit; ㄎㄢˋ ˙ㄎㄢ = try for a while.
    # Only the second 看 differs, so this resolver is position-specific.
    if ch == "看" and target_pos > 0 and line_text[target_pos - 1:target_pos + 1] == "看看":
        prev = line_text[target_pos - 2] if target_pos >= 2 else ""
        # A preceding lexical verb (e.g. 檢視看看) can plausibly be the
        # 簡編本「暫且試試」sense, but the current visible context alone is not
        # strong enough to exclude the full-tone sense. Keep it unresolved.
        if prev in {"視", "嚐", "嘗", "試", "瞧", "聽"}:
            return None
        # Standalone 看看 governing an object/clause is the observe/view sense.
        after = line_text[target_pos + 1:]
        if prev not in {"視", "嚐", "嘗", "試", "瞧", "聽"} and (
            after.startswith(("吧", "能否", "你的", "你們", "照片", "情況", "風景", "老師"))
            or prev in {"你", "，", ",", "。", "！", "!"}
        ):
            return RuleDecision(
                expected_reading="ㄎㄢˋ", rule_id="MOE-SENSE-KANKAN-VIEW",
                rule_mode="semantic_word", matched_phrase="看看（觀察／觀賞義，第二字）", source=source, source_url="",
                note="簡編本『看看』觀察／觀賞／探訪義讀ㄎㄢˋ ㄎㄢˋ；現句由可見受詞／子句語境唯一化。",
                priority=170, evidence_level="moe_direct_context",
            )
    return None


def resolve_mama(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve the two occurrences in 媽媽 independently.

    MOE Revised Dictionary lists 媽媽 as ㄇㄚ ˙ㄇㄚ. This function never
    consults the PDF actual reading; it only uses the visible lexical position.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "媽":
        return None
    url = "https://dict.revised.moe.edu.tw/dictView.jsp?ID=26699&la=0&powerMode=0"
    if line_text[target_pos:target_pos + 2] == "媽媽":
        return RuleDecision(
            expected_reading="ㄇㄚ", rule_id="V14-MOE-MAMA-1", rule_mode="special_repeat",
            matched_phrase="媽媽（第一字）", source="教育部《重編國語辭典修訂本》", source_url=url,
            note="完整詞條『媽媽』列音 ㄇㄚ ˙ㄇㄚ；此為第一字。", priority=160,
        )
    if target_pos > 0 and line_text[target_pos - 1:target_pos + 1] == "媽媽":
        return RuleDecision(
            expected_reading="˙ㄇㄚ", rule_id="V14-MOE-MAMA-2", rule_mode="special_repeat",
            matched_phrase="媽媽（第二字）", source="教育部《重編國語辭典修訂本》", source_url=url,
            note="完整詞條『媽媽』列音 ㄇㄚ ˙ㄇㄚ；此為第二字輕聲。", priority=160,
        )
    return None

def reading_options(reading: str) -> tuple[str, ...]:
    """Split one or more acceptable Bopomofo readings separated by ； or ;."""
    vals = []
    for part in str(reading or "").replace(";", "；").split("；"):
        part = part.strip()
        if part and part not in vals:
            vals.append(part)
    return tuple(vals)


def reading_matches(actual: str, expected: str) -> bool:
    return str(actual or "").strip() in reading_options(expected)



def resolve_reduplication(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve high-confidence kinship reduplications by lexical position.

    This uses only the visible word position, never the observed PDF reading.
    """
    if target_pos is None or target_pos < 0 or target_pos >= len(line_text or ""):
        return None
    ch = line_text[target_pos]
    specs = {
        "媽": ("媽媽", "ㄇㄚ", "˙ㄇㄚ", "教育部《重編國語辭典修訂本》"),
        "姐": ("姐姐", "ㄐㄧㄝˇ", "˙ㄐㄧㄝ", "教育部《重編國語辭典修訂本》"),
        "弟": ("弟弟", "ㄉㄧˋ", "˙ㄉㄧ", "教育部《國語辭典簡編本》"),
    }
    spec = specs.get(ch)
    if not spec:
        return None
    phrase, first, second, source = spec
    if line_text[target_pos:target_pos + 2] == phrase:
        return RuleDecision(
            expected_reading=first, rule_id=f"SPECIAL-REDUP-{ch}-1", rule_mode="special_repeat",
            matched_phrase=f"{phrase}（第一字）", source=source, source_url="",
            note=f"完整詞語「{phrase}」的第一字讀 {first}。", priority=165,
        )
    if target_pos > 0 and line_text[target_pos - 1:target_pos + 1] == phrase:
        return RuleDecision(
            expected_reading=second, rule_id=f"SPECIAL-REDUP-{ch}-2", rule_mode="special_repeat",
            matched_phrase=phrase, source=source, source_url="",
            note=f"完整詞語「{phrase}」的第二字為輕聲 {second}。", priority=165,
        )
    return None


def resolve_ge_classifier(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Conservative neutral-tone rule for 個 used as a measure word.

    The project dictionary explicitly notes that measure-word 個 is commonly
    neutral in ordinary speech (e.g. 一個、這個、那個).  Lexical 個人/個性/個子
    are intentionally not covered by this resolver.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "個":
        return None
    prev = line_text[target_pos - 1] if target_pos > 0 else ""
    # Numerals, demonstratives and common quantifiers directly before 個.
    triggers = set("〇零一二三四五六七八九十百千萬億半每這那哪幾某整各兩俩兩俩")
    if prev in triggers or line_text[max(0, target_pos - 1):target_pos + 1] in {"是個", "當個", "來個", "添個"}:
        return RuleDecision(
            expected_reading="ㄍㄜˋ；˙ㄍㄜ", rule_id="SPECIAL-GE-CLASSIFIER", rule_mode="special_ge",
            matched_phrase=line_text[max(0, target_pos - 1):min(len(line_text), target_pos + 2)],
            source="一字多音-字典檔.xlsx", source_url="",
            note="專案字典明列：個作量詞時一般口語多變讀輕聲；教材亦可保留本調，因此ㄍㄜˋ與˙ㄍㄜ皆接受。", priority=125,
        )
    return None



def resolve_ya_particle(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve sentence-final particle 呀 conservatively as neutral tone.

    MOE 國語小字典 distinguishes interjection/onomatopoeic ㄧㄚ from the
    sentence-final particle ˙ㄧㄚ.  We only fire when 呀 follows visible clause
    text and is immediately sentence-final (end of line or terminal punctuation).
    Common interjection compounds such as 哎呀／唉呀／啊呀／喔呀／哦呀 are
    explicitly excluded.  No observed PDF pronunciation participates.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "呀":
        return None
    if target_pos == 0:
        return None
    prev = line_text[target_pos - 1]
    if prev in "哎唉啊喔哦呀":
        return None
    nxt = line_text[target_pos + 1: target_pos + 2]
    if nxt and nxt not in "。！？!?；;":
        return None
    return RuleDecision(
        expected_reading="˙ㄧㄚ",
        rule_id="SPECIAL-YA-SENTENCE-FINAL",
        rule_mode="special_ya",
        matched_phrase=line_text[max(0, target_pos - 3): min(len(line_text), target_pos + 2)],
        source="教育部《國語小字典》",
        source_url="https://dict.mini.moe.edu.tw/SearchIndex/word_detail?breadcrumbs=Search_%E5%91%80_one&dictSearchField=%E5%91%80&wordID=D0003441",
        note="教育部國語小字典將句末助詞『呀』列為輕聲˙ㄧㄚ（如『媽呀！』『真的呀！』『好險呀！』）；僅在可見句末助詞位置套用。",
        priority=155,
        evidence_level="moe_direct",
    )


def resolve_fen_numeric_unit(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve 分 after an explicit numeral as ㄈㄣ.

    MOE Revised Dictionary lists ㄈㄣ for numerical units and score/degree
    quantities, while ㄈㄣˋ covers status/relationship/component senses.
    Requiring an immediately preceding numeral makes this rule conservative.
    """
    if target_pos is None or target_pos <= 0 or not line_text or line_text[target_pos] != "分":
        return None
    prev = line_text[target_pos - 1]
    numerals = set("〇零一二三四五六七八九十百千萬億0123456789")
    if prev not in numerals:
        return None
    return RuleDecision(
        expected_reading="ㄈㄣ",
        rule_id="SPECIAL-FEN-NUMERIC-UNIT",
        rule_mode="special_fen",
        matched_phrase=line_text[max(0, target_pos - 2): min(len(line_text), target_pos + 2)],
        source="教育部《重編國語辭典修訂本》2021",
        source_url="https://dict.revised.moe.edu.tw/dictView.jsp?ID=1688&la=0&powerMode=0",
        note="教育部辭典『分』ㄈㄣ義包含各類計量與分數／程度單位；此規則僅在前一字為明確數字時套用。",
        priority=150,
        evidence_level="moe_direct",
    )

def resolve_bu(
    line_text: str,
    target_pos: int | None,
    resolve_next_expected,
) -> RuleDecision | None:
    """Resolve 不 sandhi without consulting the observed PDF reading.

    Before a fourth-tone syllable, 不 -> ㄅㄨˊ; otherwise the base form ㄅㄨˋ.
    The following syllable must be independently resolved before applying sandhi.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "不":
        return None
    if target_pos + 1 >= len(line_text):
        return None
    next_ch = line_text[target_pos + 1]
    next_decision = resolve_next_expected(next_ch, target_pos + 1)
    if not next_decision:
        return None
    tone = tone_class(next_decision.expected_reading)
    if tone is None:
        return None
    expected = "ㄅㄨˊ；ㄅㄨˋ" if tone == 4 else "ㄅㄨˋ"
    return RuleDecision(
        expected_reading=expected, rule_id="SPECIAL-BU-SANDHI", rule_mode="special_bu",
        matched_phrase=line_text[target_pos:target_pos + 2],
        source="現代國語語流變調規則（由後字獨立讀音觸發）", source_url="",
        note=f"後字獨立判讀為 {next_decision.expected_reading}；第{tone}聲。四聲前的語流變調ㄅㄨˊ與教材保留本調ㄅㄨˋ均接受。", priority=125,
    )


def tone_class(reading: str) -> int | None:
    """Return Mandarin tone 1..5; alternatives are valid only if all share a tone."""
    tones = set()
    for s in reading_options(reading):
        if not s:
            continue
        if "˙" in s:
            tones.add(5)
        elif "ˊ" in s:
            tones.add(2)
        elif "ˇ" in s:
            tones.add(3)
        elif "ˋ" in s:
            tones.add(4)
        else:
            tones.add(1)
    return next(iter(tones)) if len(tones) == 1 else None


def resolve_yi_base_context(line_text: str, target_pos: int | None) -> RuleDecision | None:
    """Resolve only explicit base-tone numeral/ordinal contexts for 一.

    This runs before older project lexical rules so a shorter rule such as
    「一名」 cannot override the containing ordinal 「第一名」.  It does not
    perform tone sandhi; generic sandhi remains a separate later step.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "一":
        return None
    prev_ch = line_text[target_pos - 1] if target_pos > 0 else ""
    next_ch = line_text[target_pos + 1] if target_pos + 1 < len(line_text) else ""
    numerals = set("〇零一二三四五六七八九0123456789")
    if prev_ch == "第" or next_ch in numerals:
        return RuleDecision(
            expected_reading="ㄧ",
            rule_id="SPECIAL-YI-BASE",
            rule_mode="special_yi",
            matched_phrase=line_text[max(0, target_pos - 1): target_pos + 2],
            source="使用者確認規則（2026-08-17）／專案數字語境規則",
            source_url="",
            note="序數或連續數字語境採本調ㄧ；此判定不讀取 PDF actual。",
            priority=1000,
        )
    return None


def resolve_yi(
    line_text: str,
    target_pos: int | None,
    resolve_next_expected,
) -> RuleDecision | None:
    """Confirmed proofreading rule for 一:
    standalone / ordinal-numeral contexts => ㄧ;
    before a fourth-tone syllable => ㄧˊ;
    before a first-, second-, or third-tone syllable => ㄧˋ.
    A neutral-tone following syllable is not covered by this user-confirmed rule
    and therefore remains unresolved unless a higher-priority exact rule applies.
    The next syllable must itself be resolved independently by the lexical rule engine.
    """
    if target_pos is None or target_pos < 0 or not line_text or line_text[target_pos] != "一":
        return None
    base = resolve_yi_base_context(line_text, target_pos)
    if base:
        return base
    next_ch = line_text[target_pos + 1] if target_pos + 1 < len(line_text) else ""
    if not next_ch:
        return None
    next_decision = resolve_next_expected(next_ch, target_pos + 1)
    if not next_decision:
        return None
    tone = tone_class(next_decision.expected_reading)
    if tone == 4:
        expected = "ㄧˊ"
    elif tone in (1, 2, 3):
        expected = "ㄧˋ"
    else:
        # 使用者目前只確認去聲、陰平、陽平、上聲前的變調；
        # 輕聲前不自行類推。
        return None
    return RuleDecision(
        expected_reading=expected,
        rule_id="SPECIAL-YI-SANDHI",
        rule_mode="special_yi",
        matched_phrase=line_text[target_pos: target_pos + 2],
        source="使用者確認規則（2026-08-17）",
        source_url="",
        note=(
            f"後字獨立判讀為{next_decision.expected_reading}（第{tone}聲）；"
            "依使用者確認規則：去聲前『一』標ㄧˊ，陰平、陽平、上聲前標ㄧˋ。"
        ),
        priority=110,
    )
