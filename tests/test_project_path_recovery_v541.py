from pathlib import Path

import standalone_proofread as sp
import standalone_gui as sg


def touch_session(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "校對工作階段.json").write_text("{}", encoding="utf-8")


def test_cli_recovers_from_duplicated_output_child(tmp_path):
    project = tmp_path / "注音校對_輸出"
    touch_session(project)
    mistaken = project / "注音校對_輸出"
    assert sp.resolve_existing_project_dir(mistaken) == project


def test_cli_recovers_from_textbook_parent(tmp_path):
    project = tmp_path / "注音校對_輸出"
    touch_session(project)
    assert sp.resolve_existing_project_dir(tmp_path) == project


def test_exact_project_path_always_wins(tmp_path):
    project = tmp_path / "注音校對_輸出"
    touch_session(project)
    nested = project / "注音校對_輸出"
    touch_session(nested)
    assert sp.resolve_existing_project_dir(nested) == nested
    assert sg.normalize_existing_project_folder(nested) == nested


def test_gui_recovers_same_two_common_mistakes(tmp_path):
    project = tmp_path / "注音校對_輸出"
    touch_session(project)
    assert sg.normalize_existing_project_folder(project / "注音校對_輸出") == project
    assert sg.normalize_existing_project_folder(tmp_path) == project


def test_no_guess_when_no_adjacent_session(tmp_path):
    path = tmp_path / "注音校對_輸出" / "注音校對_輸出"
    assert sp.resolve_existing_project_dir(path) == path
    assert sg.normalize_existing_project_folder(path) == path
