"""Non-window public entrypoint wiring and real alias preflight refusals."""
import json
import sys
from pathlib import Path

import pytest
import fitz

import pdf_portability as portable
import standalone_gui as gui
import standalone_proofread as sp
from tests.test_pdf_portability import pdf, project, add_override, reseal_actual_dynamic


class Value:
    def __init__(self, value):
        self.value = str(value)
        self.writes = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = str(value)
        self.writes.append(str(value))


def _surface(current):
    app = gui.App.__new__(gui.App)
    app.input, app.output = Value("old-input"), Value(current)
    app._status_generation = 11
    app.started = []
    app.start = lambda command: app.started.append(command)
    return app


def _source(tmp_path, name, *, saved_actual=True, moved=True):
    source_pdf = tmp_path / (name + ".pdf")
    pdf(source_pdf, title=name)
    source = tmp_path / (name + " project")
    manifest, _ = project(source, source_pdf, session=name, genuine_source=True)
    if saved_actual:
        add_override(source, manifest["records"][0])
        reseal_actual_dynamic(source, source_pdf)
    portable.prepare_portable_project(source)
    alias = source_pdf.rename(tmp_path / (name + " moved original.pdf")) if moved else source_pdf
    return source, alias


def _dialogs(monkeypatch, directories, files):
    directories, files = iter(directories), iter(files)
    prompts, errors = [], []
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **_kwargs: str(next(directories)))

    def choose(**kwargs):
        prompts.append(kwargs)
        value = next(files)
        return value() if callable(value) else str(value)

    monkeypatch.setattr(gui.filedialog, "askopenfilename", choose)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))
    return prompts, errors


def test_cli_alias_tokens_reach_public_merge_dispatch(tmp_path, monkeypatch, capsys):
    source, second = tmp_path / "source project", tmp_path / "來源 二"
    local, target = tmp_path / "local PDF.pdf", tmp_path / "output project"
    values = [[str(source), "session A", "a" * 64, str(tmp_path / "原檔 A.pdf")],
              [str(second), "session B", "b" * 64, str(tmp_path / "原檔 B.pdf")]]
    observed = []
    monkeypatch.setattr(portable, "merge_projects", lambda *args, **kwargs:
                        (observed.append((args, kwargs)), {"dispatched": True})[1])
    monkeypatch.setattr(sys, "argv", ["standalone_proofread.py", "--continue-from-project", str(source),
        "--merge-with-project", str(second), "--local-pdf", str(local), "-o", str(target),
        *[token for row in values for token in ("--source-pdf-alias", *row)]])
    assert sp.main() == 0
    assert json.loads(capsys.readouterr().out) == {"dispatched": True}
    assert observed == [(([source, second], local, target), {"source_pdf_aliases": [
        dict(zip(("source_project", "session_id", "pdf_sha256", "path"), row)) for row in values]})]
    assert not target.exists()  # Dispatch capture is not a writer qualification test.


def test_cli_alias_rejected_outside_continue(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["standalone_proofread.py", "--prepare-portable", "-o", str(tmp_path),
        "--source-pdf-alias", "source", "session", "a" * 64, "original.pdf"])
    with pytest.raises(SystemExit) as failure:
        sp.main()
    assert failure.value.code == 2
    assert "僅適用 --continue-from-project" in capsys.readouterr().err


def test_gui_missing_source_alias_wrong_sha_never_starts(tmp_path, monkeypatch):
    source, original = _source(tmp_path, "A")
    other = tmp_path / "same picture different SHA.pdf"
    other.write_bytes(original.read_bytes())
    with fitz.open(other) as document:
        document.set_metadata({"title": "different bytes, unchanged page paints"})
        document.saveIncr()
    target = tmp_path / "new target"
    app = _surface("initial project")
    watched = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
    prompts, errors = _dialogs(monkeypatch, [source, target], [other, other])
    app.continue_portable()
    assert len(prompts) == 2 and len(errors) == 1
    assert "SOURCE_PDF_SHA_MISMATCH" in errors[0][1]
    request, = portable.source_pdf_alias_requests([source])
    assert all(str(request[key]) in prompts[-1]["title"]
               for key in ("source_project", "session_id", "pdf_sha256", "pdf_name"))
    assert not app.started and not app.input.writes and not app.output.writes
    assert not target.exists() and original.exists()
    assert {path: path.read_bytes() for path in watched} == watched


def test_gui_alias_cancel_and_selection_change_never_start(tmp_path, monkeypatch):
    source, original = _source(tmp_path, "A")
    target = tmp_path / "new target"
    app = _surface("initial project")
    _prompts, errors = _dialogs(monkeypatch, [source, target], [original, ""])
    app.continue_portable()
    assert not errors and not app.started and not app.input.writes and not app.output.writes

    current, _ = _source(tmp_path, "current", saved_actual=False, moved=False)
    app = _surface(current)

    def choose_after_reselection():
        # The output text returns to A, while the real selection generation
        # records A -> B -> A. No stale command may start against that A.
        app._status_generation += 2
        return str(original)

    _prompts, errors = _dialogs(monkeypatch, [source, target], [original, choose_after_reselection])
    app.import_project_decisions()
    assert len(errors) == 1 and "目前專案已變更" in errors[0][1]
    assert not app.started and not app.input.writes and not app.output.writes and not target.exists()


def test_gui_original_source_and_expected_only_need_no_alias(tmp_path, monkeypatch):
    for name, saved_actual, moved in (("original", True, False), ("expected only", False, True)):
        source, local_pdf = _source(tmp_path, name, saved_actual=saved_actual, moved=moved)
        assert portable.source_pdf_alias_requests([source]) == []
        target = tmp_path / (name + " target")
        app = _surface("initial project")
        prompts, errors = _dialogs(monkeypatch, [source, target], [local_pdf])
        app.continue_portable()
        assert len(prompts) == 1 and not errors
        assert app.started == [app.proof_cmd(["--continue-from-project", str(source),
            "--local-pdf", str(local_pdf), "-o", str(target)])]
        assert app.input.value == str(local_pdf) and app.output.value == str(target)
        assert not target.exists()  # No subprocess or Tk root is started.


def test_gui_valid_alias_reaches_command_as_four_separate_tokens(tmp_path, monkeypatch):
    source, original = _source(tmp_path, "來源 with spaces")
    target = tmp_path / "new target"
    app = _surface("initial project")
    _prompts, errors = _dialogs(monkeypatch, [source, target], [original, original])
    app.continue_portable()
    request, = portable.source_pdf_alias_requests([source])
    assert not errors
    assert app.started == [app.proof_cmd(["--continue-from-project", str(source),
        "--local-pdf", str(original), "-o", str(target), "--source-pdf-alias",
        request["source_project"], request["session_id"], request["pdf_sha256"], str(original)])]
    assert not target.exists()
