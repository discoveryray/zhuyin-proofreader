"""Synthetic local originals only; no real review, CI, windows or authorization."""
from copy import deepcopy
import hashlib
import json

from scripts import pr_review_gate as gate
from test_current_validation_evidence import bundle
from test_validation_evidence import staged_state


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {"ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(root):
    coverage = bundle(root)
    state = staged_state(coverage)
    state["validation_policy"] = gate.CURRENT_POLICY
    state["pr"]["head_branch"] = state["task"]["head_branch"] = state["pr_ci"]["branch"] = "codex/fixture"
    names = ("Check out repository", "Set up Python", "Show Python version", "Upgrade pip",
             "Install development dependencies", "Validate runtime asset integrity", "Audit test entrypoint coverage",
             "Run full pytest suite", "Verify active test execution", "Compile Python sources",
             "Check committed whitespace (pull request)", "Check repository working tree", "Capture main full environment",
             "Produce current validation coverage", "Verify current validation coverage", "Upload main full evidence",
             "Verify required validation results")
    state["pr_ci"]["jobs"] = [dict(state["pr_ci"]["jobs"][0], steps={n: "success" for n in names})]
    for i, code in enumerate(state["reviews"]):
        code["report_ref"] = str(root / ("code-%d.md" % i))
    for code in state["reviews"][:]:
        blocked = deepcopy(code)
        blocked.update(verdict="BLOCKED", blocker_kind="evidence", findings=["synthetic missing evidence"],
                       report_ref=code["report_ref"].replace("code-", "blocked-"))
        if blocked["round"] == 2:
            blocked["ci"] = {k: state["pr_ci"][k] for k in ("run_id", "attempt", "tested_sha")}
        state["reviews"].append(blocked)
    artifacts = []
    for i, review in enumerate(state["reviews"]):
        from pathlib import Path
        report = Path(review["report_ref"])
        report.write_text("SYNTHETIC independent review\n" + json.dumps(review), encoding="utf-8")
        artifacts.append({"report_ref": str(report), "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                          "record": write(root / ("record-%d.json" % i), review)})
    anchor = write(root / "original-state.json", state)
    document = {"schema": gate.BLOCKER_LINKS_SCHEMA, "task": deepcopy(state["task"]),
                "anchor": anchor, "previous": None, "links": []}
    for i in (0, 1):
        code, blocked = state["reviews"][i], state["reviews"][i + 2]
        document["links"].append({**{f: code[f] for f in ("round", "baseline", "base", "head", "scope")},
            "code_reviewer": code["reviewer"], "blocked_reviewer": blocked["reviewer"],
            "code": artifacts[i], "blocked": artifacts[i + 2]})
    state["review_history_links"] = write(root / "links.json", document)
    return state, document


def repin(root, state, document):
    state["review_history_links"] = write(root / "links-changed.json", document)


def supplement(state, number):
    old = state["reviews"][number + 1]
    fresh = deepcopy(old)
    fresh.update(verdict="PASS", blocker_kind=None, findings=[], report_ref=old["report_ref"] + "/supplement",
                 supersedes_report_ref=old["report_ref"], resolution_evidence_ref="fixture://retained-resolution",
                 coverage_sha256=hashlib.sha256(__import__("pathlib").Path(state["coverage"]).read_bytes()).hexdigest(),
                 review_mode="evidence_gap", full_diff_reviewed=False)
    state["reviews"].append(fresh)
    return fresh


def test_link_retires_only_code_and_preserves_complete_original_prefix(tmp_path):
    state, _ = fixture(tmp_path)
    before = deepcopy(state["reviews"])
    gate.validate_state(state)
    assert gate.next_action(state)["action"] == "REFRESH_EVIDENCE"
    assert [gate._review(state, n, state["current"]["base"], state["current"]["head"])["verdict"] for n in (1, 2)] == ["BLOCKED", "BLOCKED"]
    assert state["reviews"] == before


def test_existing_noncode_supplement_needs_both_rounds_and_complete_current_evidence(tmp_path):
    state, _ = fixture(tmp_path)
    supplement(state, 1)
    assert gate.next_action(state)["action"] == "REFRESH_EVIDENCE"
    supplement(state, 2)
    assert gate.next_action(state)["action"] == "MERGE_PROPOSAL"
    for mutate in (lambda s: s["reviews"][-1].update(resolution_evidence_ref=None),
                   lambda s: s.update(coverage=None),
                   lambda s: s["pr_ci"].update(conclusion="failure"),
                   lambda s: s["pr_ci"]["jobs"][0]["steps"].update({"Verify active test execution": "skipped"}),
                   lambda s: s["reviews"][-1].update(coverage_sha256="f" * 64)):
        changed = deepcopy(state); mutate(changed)
        assert gate.next_action(changed)["action"] != "MERGE_PROPOSAL"


def test_code_blocker_cannot_be_superseded_at_same_head(tmp_path):
    state, document = fixture(tmp_path)
    blocked = state["reviews"][2]
    blocked["blocker_kind"] = "code"
    # Synthetic originals are created before adoption; pin this independent fixture.
    document["links"][0]["blocked"]["record"] = write(tmp_path / "code-blocker-record.json", blocked)
    from pathlib import Path
    report = Path(blocked["report_ref"])
    report.write_text("SYNTHETIC code blocker\n" + json.dumps(blocked), encoding="utf-8")
    document["links"][0]["blocked"]["report_sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
    document["anchor"] = write(tmp_path / "code-blocker-anchor.json", {k: v for k, v in state.items() if k != "review_history_links"})
    repin(tmp_path, state, document)
    assert gate.next_action(state)["action"] == "CORRECT_IMPLEMENTATION"
    supplement(state, 1)
    assert gate.next_action(state)["action"] == "STOP"


def test_retired_code_cannot_start_parallel_finalization(tmp_path):
    state, _ = fixture(tmp_path)
    fresh = deepcopy(state["reviews"][0])
    fresh.update(report_ref="fixture://parallel", finalizes_report_ref=fresh["report_ref"],
                 resolution_evidence_ref="fixture://invented")
    state["reviews"].append(fresh)
    assert gate.next_action(state)["action"] == "STOP"


def test_anchored_history_cannot_be_deleted_reordered_or_edited(tmp_path):
    state, _ = fixture(tmp_path)
    for mutate in (lambda s: s["reviews"].pop(0), lambda s: s["reviews"].reverse(),
                   lambda s: s["reviews"][2]["findings"].clear(), lambda s: s["implementers"].clear(),
                   lambda s: s["reviews"][0].update(supersedes_report_ref="fixture://invented")):
        changed = deepcopy(state); mutate(changed)
        assert gate.next_action(changed)["action"] == "STOP"


def test_invalid_relations_and_originals_fail_closed(tmp_path):
    state, document = fixture(tmp_path)
    mutations = (
        lambda d: d["task"].update(id="foreign"),
        lambda d: d["task"].update(repository="foreign/repo"),
        lambda d: d["links"].append(deepcopy(d["links"][0])),
        lambda d: d["links"][0].update(head="f" * 40),
        lambda d: d["links"][0].update(round=2),
        lambda d: d["links"][0].update(code_reviewer="impostor"),
        lambda d: d["links"][0]["code"].update(report_ref="unknown"),
        lambda d: d["links"][0]["code"].update(report_sha256="0" * 64),
        lambda d: d["links"][0]["code"]["record"].update(sha256="0" * 64),
        lambda d: d["links"][0].update(code=deepcopy(d["links"][0]["blocked"])),
        lambda d: d["links"][0].update(code=deepcopy(d["links"][1]["code"])),
        lambda d: d.update(schema="future/99"),
    )
    for mutate in mutations:
        changed, doc = deepcopy(state), deepcopy(document); mutate(doc); repin(tmp_path, changed, doc)
        assert gate.next_action(changed)["action"] == "STOP"
    missing = deepcopy(state); missing["review_history_links"]["ref"] += ".missing"
    assert gate.next_action(missing)["action"] == "STOP"


def test_improper_reviewers_and_fabricated_record_rejected(tmp_path):
    state, document = fixture(tmp_path)
    for mutate in (lambda s: s["implementers"].append(s["reviews"][2]["reviewer"]),
                   lambda s: s["reviews"][2].update(independent=False),
                   lambda s: s["reviews"][2].update(reviewer=s["reviews"][3]["reviewer"])):
        changed = deepcopy(state); mutate(changed)
        assert gate.next_action(changed)["action"] == "STOP"
    fake = deepcopy(document)
    fake["links"][0]["code"]["record"] = write(tmp_path / "fake.json", {"verdict": "CODE_REVIEWED"})
    repin(tmp_path, state, fake)
    assert gate.next_action(state)["action"] == "STOP"


def test_duplicate_pass_and_cross_head_reuse_cannot_release(tmp_path):
    state, _ = fixture(tmp_path)
    supplement(state, 1); supplement(state, 2)
    duplicate = deepcopy(state["reviews"][-1])
    duplicate.update(report_ref="fixture://duplicate-pass", supersedes_report_ref=None,
                     resolution_evidence_ref=None, review_mode="full_diff", full_diff_reviewed=True,
                     coverage_sha256="f" * 64)
    state["reviews"].append(duplicate)
    assert gate.next_action(state)["action"] == "STOP"
    state["reviews"].pop()
    state["current"]["head"] = "f" * 40
    assert gate.next_action(state)["action"] != "MERGE_PROPOSAL"


def test_links_append_chain_cannot_drop_prior_links_or_reanchor(tmp_path):
    state, document = fixture(tmp_path)
    initial = deepcopy(document); initial["links"] = initial["links"][:1]
    extended = deepcopy(document)
    extended["previous"] = write(tmp_path / "links-initial.json", initial)
    repin(tmp_path, state, extended)
    assert gate.next_action(state)["action"] == "REFRESH_EVIDENCE"
    previous = deepcopy(state["review_history_links"])
    for mutate in (lambda d: d["links"].pop(0), lambda d: d["links"].reverse(),
                   lambda d: d.update(anchor={"ref": "invented", "sha256": "0" * 64})):
        changed, doc = deepcopy(state), deepcopy(document)
        doc["previous"] = previous; mutate(doc); repin(tmp_path, changed, doc)
        assert gate.next_action(changed)["action"] == "STOP"


def test_unadopted_v4_and_legacy_v3_keep_original_rejection(tmp_path):
    state, _ = fixture(tmp_path)
    state.pop("review_history_links")
    assert gate.next_action(state)["action"] == "STOP"
    from test_pr_review_gate import evidence_state
    legacy = evidence_state()
    before = gate.next_action(legacy)
    assert before["action"] == "MERGE_PROPOSAL"
    legacy["review_history_links"] = {"ref": "not-adopted", "sha256": "0" * 64}
    assert gate.next_action(legacy)["action"] == "STOP"


def test_original_bytes_and_duplicate_json_keys_are_not_self_attested(tmp_path):
    state, document = fixture(tmp_path)
    from pathlib import Path
    report = Path(document["links"][0]["code"]["report_ref"])
    report.write_bytes(report.read_bytes() + b"tampered")
    assert gate.next_action(state)["action"] == "STOP"
    original = Path(document["anchor"]["ref"])
    original.write_bytes(original.read_bytes() + b"tampered")
    assert gate.next_action(state)["action"] == "STOP"
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema":1,"schema":2}', encoding="utf-8")
    state["review_history_links"] = {"ref": str(invalid), "sha256": hashlib.sha256(invalid.read_bytes()).hexdigest()}
    assert gate.next_action(state)["action"] == "STOP"
