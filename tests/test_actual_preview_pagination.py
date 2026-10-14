from __future__ import annotations

import copy
import csv
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import pytest
import actual_review as actual
import review_display as display
import review_gui as gui
import global_glyph_promotion as promotion
from review_operation_worker import preview_png
from tests.no_window_review_support import Surface, Value, Preview
from tests.test_manual_actual_batch_apply_v570 import stage


class PageDialog(Surface, gui.ActualReadingDialog):
    """Only native widget boundaries are substitutes; page/preview gates run unchanged."""
    def __init__(self, samples, folder):
        Surface.__init__(self)
        self.samples, self.entry = samples, samples[0]
        self.group, self.output_dir = {"members": samples}, folder
        count = len(samples)
        self.checked_vars = [gui.OccurrenceCheckState(s["occurrence_id"]) for s in samples]
        self.previously_checked = [False] * count
        self.previews = [None] * count
        self.sample_available = [False] * count
        self._viewed_target = [False] * count
        self._preview_ready_after = [None] * count
        self.preview_slots = [None] * count
        self.preview_buttons = [None] * count
        self.check_buttons = [None] * count
        self._sample_frames = []
        self.sample_container = Surface(self)
        self.page_label = Surface(self)
        self.previous_page_button, self.next_page_button = Surface(self), Surface(self)
        self.body_canvas = Surface(self)
        self.body_canvas.yview_moveto = lambda *_: None
        self.body_canvas.request_layout = lambda: None
        self.body_canvas.reveal = lambda *_: None
        self.body_canvas.flush_layout = lambda: True
        self._visibility_after = self._active_peer = None
        self._destroying = False
        self.result = None
        self.reading, self.note = Value("", "ㄅ"), Value("", "note")

    def destroy(self):
        self._cancel_owned_preview_callbacks()
        for state in self.checked_vars:
            state.unbind()
        Surface.destroy(self)


def sample_pdf(folder):
    from tests.test_actual_source_visual_safety import make_visual_source
    return make_visual_source(folder, count=1)[0]


def page_views():
    from contextlib import ExitStack
    stack = ExitStack()
    for name in ("LabelFrame", "Frame", "Button", "Checkbutton"):
        stack.enter_context(patch.object(gui.tk, name, Surface))
    stack.enter_context(patch.object(gui.tk, "BooleanVar", Value))
    stack.enter_context(patch.object(gui, "WrappedLabel", Surface))
    stack.enter_context(patch.object(gui, "wrap_checkbutton", lambda widget: widget))
    stack.enter_context(patch.object(gui, "OccurrencePreview", Preview))
    return stack


def test_599_pages_run_real_mount_unmount_and_keep_occurrence_checks(tmp_path):
    source = sample_pdf(tmp_path)
    samples = [{**source, "occurrence_id": str(i), "review_id": f"review-{i}"} for i in range(599)]
    dialog = PageDialog(samples, tmp_path)
    with page_views():
        dialog.show_sample_page(0)
        dialog.update()
        assert dialog.sample_available[0]
        dialog.checked_vars[0].set(True)
        seen = []
        first_preview = dialog.previews[0]
        retired_invalidation = first_preview.on_invalidated
        for page in range(30):
            dialog.show_sample_page(page)
            assert len(dialog._sample_frames) <= 20
            assert sum(slot is not None for slot in dialog.preview_slots) <= 20
            assert sum(state.variable is not None for state in dialog.checked_vars) <= 20
            seen.extend(dialog._page_indices)
            if page in (10, 20, 29):
                index = dialog._page_indices[-1]
                dialog.show_sample_preview(index)
                dialog.previews[index].canvas.winfo_rooty = lambda: 0
                dialog.update()
                assert dialog.sample_available[index]
                dialog.checked_vars[index].set(True)
        assert seen == list(range(599))
        assert first_preview._disposed
        retired_invalidation()  # A queued callback from the unloaded page cannot revoke A.
        assert dialog.checked_vars[0].get()
        assert dialog.checked_vars[598].occurrence_id == "598"
        dialog.show_sample_page(10)
        assert dialog.checked_vars[219].get()
        dialog.show_sample_page(0)
        assert dialog.checked_vars[0].get()
        assert not dialog.checked_vars[1].get()
        assert dialog.reading.get() == "ㄅ"
        dialog.submit()
        assert dialog.result["checked_occurrence_ids"] == ["0", "219", "419", "598"]
        assert not dialog.callbacks


def test_only_a_submission_keeps_single_occurrence_quorum(tmp_path):
    source = sample_pdf(tmp_path)
    samples = [{**source, "occurrence_id": str(i), "review_id": f"review-{i}"} for i in range(599)]
    dialog = PageDialog(samples, tmp_path)
    with page_views():
        dialog.show_sample_page(0)
        dialog.update()
        dialog.checked_vars[0].set(True)
        dialog.show_sample_page(29)
        dialog.submit()
    assert dialog.result["checked_occurrence_ids"] == ["0"]
    from tests.test_actual_source_visual_safety import make_visual_source
    members = make_visual_source(tmp_path / "quorum-source", count=3)
    current = actual.build_actual_group_for_entry(members, members[0])
    stage(tmp_path / "actual", current, "ㄅ")
    result = actual.apply_staged_manual_actual_batch(tmp_path / "actual", [current])
    assert not result["group_results"][0]["propagated_to_group"]
    assert result["group_results"][0]["target_occurrence_ids"] == [current["members"][0]["occurrence_id"]]


def test_source_location_contradictions_disable_target_and_writes(tmp_path):
    source = sample_pdf(tmp_path)
    source["source_record"] = {"字元": "B", "實體頁碼": 1, "x0": 20}
    pix, target, notice = display.occurrence_preview(source, display_width=200)
    assert pix.width == 200 and target is None
    assert "來源與位置矛盾" in notice
    current = actual.build_actual_group_for_entry([source], source)
    with pytest.raises(ValueError, match="來源與位置"):
        actual.stage_manual_actual_group(tmp_path / "actual", current, "ㄅ", checked_occurrence_ids=["a"], source="direct")
    with pytest.raises(ValueError, match="來源與位置"):
        actual.apply_verified_actual_group(tmp_path / "actual", current, "ㄅ", checked_occurrence_ids=["a"], source="direct")
    assert not (tmp_path / "actual").exists()
    for field, value in (("physical_page", 2), ("x0", 21), ("occurrence_id", "other"), ("review_id", "other")):
        record = {**source, "source_record": {field: value}}
        assert field in actual.actual_source_consistency_errors(record)


def test_same_exact_different_readings_survive_separate_staged_transactions(tmp_path):
    from tests.test_actual_source_visual_safety import make_visual_source
    members = make_visual_source(tmp_path / "conflict-source", count=2)
    current = actual.build_actual_group_for_entry(members, members[0])
    original = copy.deepcopy(current)
    root = tmp_path / "actual"
    ids = [row["occurrence_id"] for row in current["members"]]
    stage(root, current, "ㄅ", checked_occurrence_ids=[ids[0]])
    first = actual.apply_staged_manual_actual_batch(root, [current])
    assert not first["group_results"][0]["propagated_to_group"]
    # Explicitly exercise the real acknowledgement boundary, not a mocked
    # successful PDF refresh. Full controller refresh remains separate evidence.
    promotion.acknowledge_project_refresh(root, promotion.project_refresh_token(root))
    stage(root, current, "ㄆ", checked_occurrence_ids=[ids[1]])
    second = actual.apply_staged_manual_actual_batch(root, [current])
    assert second["group_results"][0]["glyph_truth_conflict"]
    assert not second["group_results"][0]["propagated_to_group"]
    with (root / actual.OCCURRENCE_OVERRIDE_FILE).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {(row["y0"], row["actual_reading"]) for row in rows} == {(str(members[0]["y0"]), "ㄅ"), (str(members[1]["y0"]), "ㄆ")}
    assert actual.load_user_verified_glyf(root / actual.USER_GLYF_FILE) == {}
    assert actual.load_manual_actual_staging(root)["staged_groups"] == []
    assert current == original  # expected chain/identity remain unchanged.


def test_unallocated_action_rows_wait_for_real_width():
    button = SimpleNamespace(winfo_exists=lambda: True, winfo_reqwidth=lambda: 70,
        winfo_reqheight=lambda: 30, place=lambda **kw: placements.append(kw))
    placements, heights = [], []
    rows = SimpleNamespace(_disposed=False, _cancel_layout=lambda: None, items=[button] * 3,
        winfo_width=lambda: 1, winfo_reqheight=lambda: 1, configure=lambda **kw: heights.append(kw))
    display.ActionRows._layout(rows)
    assert not placements and not heights
    rows.winfo_width = lambda: 300
    display.ActionRows._layout(rows)
    assert [p["y"] for p in placements] == [3, 3, 3]
    assert heights == [{"height": 36}]


def test_item_switch_rejects_late_real_raster_result(tmp_path):
    source = sample_pdf(tmp_path)
    preview = Preview(Surface())
    preview._entry, preview._output_dir, preview._padding = copy.deepcopy(source), None, (20, 20)
    preview._render_width = 200
    preview._preview_generation = 1
    old_token = (1, copy.deepcopy(source), None, 200)
    old_result = preview_png(source, padding=(20, 20), output_dir=None, display_width=200)
    preview._preview_token = old_token
    preview._preview_job = SimpleNamespace(poll=lambda: old_result)
    # A -> B switch happens before A's raster response arrives. Both the
    # generation and the occurrence-owned snapshot are checked by production.
    preview._entry = {**source, "occurrence_id": "b", "review_id": "review-b"}
    preview._preview_generation = 2
    display.OccurrencePreview._poll_preview(preview)
    assert preview.photo is None and preview.target is None
    assert preview._entry["occurrence_id"] == "b"
    # Even a current token cannot accept pixels labelled with another target.
    preview._preview_token = (2, copy.deepcopy(preview._entry), None, 200)
    preview._preview_job = SimpleNamespace(poll=lambda: old_result)
    display.OccurrencePreview._poll_preview(preview)
    assert preview.photo is None and preview._entry is None
    assert "預覽結果不屬於目前封印與位置" in preview.notice.cget("text")
    preview.destroy()
