from pronunciation_rule_engine import resolve_company_position_rule, resolve_semantic_complete_word_rule


def test_baobao_company_second_is_neutral_and_first_not_overridden():
    text = "小寶寶出生"
    first = text.index("寶")
    second = first + 1
    assert resolve_company_position_rule(text, first) is None
    hit = resolve_company_position_rule(text, second)
    assert hit is not None
    assert hit.expected_reading == "˙ㄅㄠ"
    assert "公司" in hit.source


def test_dongxi_object_sense_uses_visible_context_only():
    text = "生活中哪些東西可以回收?"
    pos = text.index("西")
    hit = resolve_semantic_complete_word_rule(text, pos)
    assert hit is not None and hit.expected_reading == "˙ㄒㄧ"
    assert resolve_semantic_complete_word_rule("東西走向", 1).expected_reading == "ㄒㄧ"


def test_haowan_fun_sense():
    text = "桌球好玩的地方"
    hit = resolve_semantic_complete_word_rule(text, text.index("好"))
    assert hit is not None and hit.expected_reading == "ㄏㄠˇ"
    assert resolve_semantic_complete_word_rule("他有些好玩", 3) is None


def test_kankan_second_position_semantics():
    text = "一起比賽,看看你的成果"
    second = text.index("看看") + 1
    hit = resolve_semantic_complete_word_rule(text, second)
    assert hit is not None and hit.expected_reading == "ㄎㄢˋ"
    text2 = "來檢視看看吧!"
    second2 = text2.index("看看") + 1
    hit2 = resolve_semantic_complete_word_rule(text2, second2)
    assert hit2 is None


def test_v56_tool_version_does_not_block_same_schema_decision_database():
    from standalone_proofread import normalize_db, VERSION
    from occurrence_ledger import SESSION_SCHEMA_VERSION, REVIEW_ID_SCHEMA_VERSION
    for old_version in ("5.2.1", "5.4.1", "5.5.0", "5.5.1"):
        original = {
            "version": old_version,
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "review_id_schema_version": REVIEW_ID_SCHEMA_VERSION,
            "events": {"rev_fixture": {"action": "解決expected證據", "expected_set": "ㄎㄢˋ"}},
        }
        upgraded = normalize_db(original)
        assert upgraded["version"] == VERSION
        assert upgraded["events"] == original["events"]


def test_v56_incompatible_schema_still_blocks_decision_database():
    from standalone_proofread import normalize_db
    from occurrence_ledger import REVIEW_ID_SCHEMA_VERSION
    original = {
        "version": "5.5.1",
        "session_schema_version": "9.0.0",
        "review_id_schema_version": REVIEW_ID_SCHEMA_VERSION,
        "events": {},
    }
    try:
        normalize_db(original)
    except ValueError as exc:
        assert "INCOMPATIBLE" in str(exc)
    else:
        raise AssertionError("incompatible schema must still be blocked")
