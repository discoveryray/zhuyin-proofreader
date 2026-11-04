"""Synthetic bridge pins only; real private originals are verified separately."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import pr_review_gate as gate
from test_pr_review_gate import evidence_state, review_evidence
from test_review_history_blocker_links import write


def commit(tree, parents):
    raw = ("tree " + tree + "\n" + "".join("parent " + p + "\n" for p in parents)
           + "author Synthetic <fixture@example.invalid> 1 +0000\n"
           + "committer Synthetic <fixture@example.invalid> 1 +0000\n\nSYNTHETIC\n").encode()
    return raw, hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def save_anchor(root, state, fixed):
    """Create/re-pin explicitly synthetic originals, never real source records."""
    anchor = {k: deepcopy(v) for k, v in state.items() if k != "review_history_links"}
    pointer = write(root / "synthetic-anchor.json", anchor)
    fixed["sha256"] = pointer["sha256"]
    document = {"schema": gate.BLOCKER_LINKS_SCHEMA, "task": deepcopy(state["task"]),
                "anchor": pointer, "previous": None, "links": []}
    artifacts = []
    for i in range(9, 13):
        review = state["reviews"][i]
        report = Path(review["report_ref"])
        report.write_bytes(b"SYNTHETIC review report\n")
        artifacts.append({"report_ref": str(report), "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                          "record": write(root / ("synthetic-record-%d.json" % i), review)})
    for a, b in ((0, 2), (1, 3)):
        code, blocked = state["reviews"][a + 9], state["reviews"][b + 9]
        document["links"].append({**{f: code[f] for f in ("round", "baseline", "base", "head", "scope")},
            "code_reviewer": code["reviewer"], "blocked_reviewer": blocked["reviewer"],
            "code": artifacts[a], "blocked": artifacts[b]})
    state["review_history_links"] = write(root / "synthetic-links.json", document)
    return document


def bridge_fixture(root, monkeypatch, defect=None, *, pin=True):
    state = evidence_state()
    state.update(schema=gate.STAGED_SCHEMA, validation_policy=gate.CURRENT_POLICY,
                 execution={"mode": "validation", "validation_authorization_ref": "fixture://start"},
                 coverage=None, short_validation=None)
    state["task"]["id"] = state["authorization"]["task_id"] = "synthetic-anchor-only"
    baseline, feature, tree = state["task"]["baseline"], "4" * 40, "5" * 40
    raw, merge = commit("6" * 40 if defect == "tree" else tree,
                        [feature, baseline] if defect == "ordered_parents" else [baseline, feature])
    candidate_raw, head = commit("7" * 40, [feature if defect == "candidate_parent" else merge])
    fixed = {"task": deepcopy(state["task"]), "feature": feature, "merge": merge, "merge_tree": tree,
             "head": head, "head_tree": "7" * 40, "originals": {}}
    # Only the synthetic fixture replaces the allowlist; no copied private reports/sessions/paths.
    for name in gate.USAGE_FEEDBACK_ANCHOR["originals"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (raw if name.endswith("prior-merge-commit.raw") else candidate_raw
                   if name.endswith("candidate-commit.raw") else ("SYNTHETIC " + name).encode())
        path.write_bytes(content)
        fixed["originals"][name] = hashlib.sha256(content).hexdigest()
    if defect == "git_sha":
        path = root / "candidate-009/prior-merge-commit.raw"
        path.write_bytes(raw + b"changed")
        fixed["originals"]["candidate-009/prior-merge-commit.raw"] = hashlib.sha256(path.read_bytes()).hexdigest()
    state["current"].update(base=merge, head=head)
    state["pr"].update(base_sha=merge, head_sha=head)
    state["corrections"] = [
        {"number": 1, "from_head": "1" * 40, "to_head": "2" * 40, "evidence_ref": "fixture://one"},
        {"number": 2, "from_head": "2" * 40, "to_head": "3" * 40, "evidence_ref": "fixture://two"},
        {"number": 3, "from_head": "3" * 40, "to_head": feature, "evidence_ref": "fixture://three"},
        {"number": 4, "from_head": merge, "to_head": head,
         "evidence_ref": str(root / "candidate-009/candidate-commit.raw")},
    ]
    reviews = []
    for i in range(9):
        review = review_evidence(1)
        review.update(head=f"{i + 10:040x}", verdict="BLOCKED", blocker_kind="contract",
                      findings=["SYNTHETIC historical finding"], report_ref=f"fixture://historical/{i}",
                      coverage_sha256=None, finalizes_report_ref=None)
        reviews.append(review)
    for i, number in enumerate((1, 2, 1, 2)):
        review = review_evidence(number)
        review.update(base=merge, head=head, report_ref=str(root / f"synthetic-report-{i}.md"),
                      verdict="CODE_REVIEWED" if i < 2 else "BLOCKED",
                      blocker_kind=None if i < 2 else "code", findings=[] if i < 2 else ["SYNTHETIC code finding"],
                      coverage_sha256=None, finalizes_report_ref=None)
        reviews.append(review)
    state["reviews"] = reviews
    document = save_anchor(root, state, fixed)
    if pin:
        monkeypatch.setattr(gate, "USAGE_FEEDBACK_ANCHOR", fixed)
    return state, document, fixed


def test_synthetic_bridge_reader_retires_only_two_codes_and_keeps_native_rejection(tmp_path, monkeypatch):
    state, document, _ = bridge_fixture(tmp_path, monkeypatch)
    before = deepcopy(state)
    retired = {state["reviews"][i]["report_ref"] for i in (9, 10)}
    assert gate._historical_code_refs(state) == retired
    gate._validate_review_history(state)
    assert [gate._review(state, n, state["current"]["base"], state["current"]["head"])["verdict"]
            for n in (1, 2)] == ["BLOCKED", "BLOCKED"]
    assert state == before
    anchor = gate._linked_json(document["anchor"])
    with pytest.raises(gate.EvidenceError, match="correction history is discontinuous"):
        gate._validate_state_structure(anchor)
    with pytest.raises(gate.EvidenceError, match="correction rounds exceed"):
        gate.validate_state(state)
    assert gate.next_action(state)["action"] == "STOP"


def test_real_pin_rejects_synthetic_anchor_without_fixture_override(tmp_path, monkeypatch):
    state, _, _ = bridge_fixture(tmp_path, monkeypatch, pin=False)
    with pytest.raises(gate.EvidenceError, match="unsupported historical bridge anchor"):
        gate._historical_code_refs(state)


@pytest.mark.parametrize("defect", ["git_sha", "ordered_parents", "tree", "candidate_parent"])
def test_bridge_checks_raw_git_identity_ordered_parents_and_tree(tmp_path, monkeypatch, defect):
    state, _, _ = bridge_fixture(tmp_path, monkeypatch, defect)
    with pytest.raises(gate.EvidenceError, match="commit object hash|parents/tree|parent/tree"):
        gate._historical_code_refs(state)


@pytest.mark.parametrize("defect", ["original_hash", "original_missing", "anchor_hash", "identity"])
def test_bridge_needs_exact_anchor_identity_and_all_originals(tmp_path, monkeypatch, defect):
    state, document, fixed = bridge_fixture(tmp_path, monkeypatch)
    if defect.startswith("original"):
        path = tmp_path / "corrective5/user-replacement-authorization-008.md"
        if defect == "original_hash":
            path.write_bytes(b"changed")
        else:
            path.unlink()
    elif defect == "anchor_hash":
        document["anchor"]["sha256"] = "f" * 64
        state["review_history_links"] = write(tmp_path / "wrong-anchor-links.json", document)
    else:
        state["task"]["id"] = state["authorization"]["task_id"] = "foreign-task"
        save_anchor(tmp_path, state, fixed)
    with pytest.raises(gate.EvidenceError):
        gate._historical_code_refs(state)


@pytest.mark.parametrize("defect", ["header_type", "correction_type", "review_fields", "review_ci", "tail_type"])
def test_bridge_reuses_complete_shared_structure(tmp_path, monkeypatch, defect):
    state, _, fixed = bridge_fixture(tmp_path, monkeypatch)
    if defect == "header_type":
        state["authorization"]["active"] = 1
    elif defect == "correction_type":
        state["corrections"][3]["number"] = 4.0
    elif defect == "review_fields":
        state["reviews"][0]["unknown"] = None
    elif defect == "review_ci":
        state["reviews"][9]["ci"] = {"run_id": 1, "attempt": 1, "tested_sha": "8" * 40}
    else:
        state["pr"]["protection_satisfied"] = 1
    save_anchor(tmp_path, state, fixed)
    with pytest.raises(gate.EvidenceError):
        gate._historical_code_refs(state)


@pytest.mark.parametrize("defect", ["review_prefix", "correction_prefix", "actors", "scope", "reviewer", "relations", "missing_md", "report_hash", "pairs"])
def test_bridge_keeps_prefix_scope_reviewer_relations_and_report_checks(tmp_path, monkeypatch, defect):
    state, document, fixed = bridge_fixture(tmp_path, monkeypatch)
    if defect == "review_prefix":
        state["reviews"][0]["findings"].append("changed")
    elif defect == "correction_prefix":
        state["corrections"][0]["evidence_ref"] = "changed"
    elif defect == "actors":
        state["implementers"].pop()
    elif defect == "scope":
        document["links"][0]["scope"] = "partial"
    elif defect == "reviewer":
        state["implementers"].append(state["reviews"][11]["reviewer"])
    elif defect == "relations":
        state["reviews"][11]["resolution_evidence_ref"] = "fixture://invented"
        document = save_anchor(tmp_path, state, fixed)
    elif defect == "missing_md":
        Path(document["links"][0]["code"]["report_ref"]).unlink()
    elif defect == "report_hash":
        Path(document["links"][0]["blocked"]["report_ref"]).write_bytes(b"changed")
    else:
        document["links"].reverse()
    state["review_history_links"] = write(tmp_path / "changed-links.json", document)
    with pytest.raises(gate.EvidenceError):
        gate._historical_code_refs(state)


def test_old_links_cannot_review_new_head_or_clear_new_blocker(tmp_path, monkeypatch):
    state, _, _ = bridge_fixture(tmp_path, monkeypatch)
    state["current"]["head"] = "9" * 40
    state["corrections"].append({"number": 5, "from_head": state["corrections"][-1]["to_head"],
                                 "to_head": "9" * 40, "evidence_ref": "fixture://new-head"})
    for number in (1, 2):
        assert gate._review(state, number, state["current"]["base"], "9" * 40) is None
        blocked = deepcopy(state["reviews"][number + 10])
        blocked.update(head="9" * 40, report_ref=f"fixture://new-head-blocker-{number}")
        state["reviews"].append(blocked)
        assert gate._review(state, number, state["current"]["base"], "9" * 40) is blocked
    gate._validate_review_history(state)
    assert gate._historical_code_refs(state) == {state["reviews"][i]["report_ref"] for i in (9, 10)}
    assert not any(r["verdict"] == "PASS" for r in state["reviews"])
