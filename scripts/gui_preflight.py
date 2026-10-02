"""Tk probe executed by validation_runner with the GUI interpreter and fd capture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import tempfile


def test_gui_environment():
    import tkinter as tk
    from tkinter import ttk
    result = {"python": platform.python_version(), "stage": "paths"}
    output = Path(os.environ["VALIDATION_PREFLIGHT_OUTPUT"])

    def save():
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    save()
    for folder in (output.parent, Path(os.environ["VALIDATION_TEMP"])):
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            probe = Path(handle.name)
            handle.write(b"validation-probe")
        try:
            assert probe.read_bytes() == b"validation-probe"
        finally:
            probe.unlink()
    result["stage"] = "tk_initialization"
    save()
    root = tk.Tk()
    try:
        root.withdraw()
        result.update(tcl_version=root.tk.call("info", "patchlevel"),
                      tk_version=root.tk.call("package", "provide", "Tk"),
                      tcl_library=root.tk.eval("info library"),
                      tk_library=root.tk.eval("set tk_library"),
                      loaded_libraries=root.tk.eval("info loaded"))
        save()
        combo = ttk.Combobox(root, values=("probe",))
        spin = ttk.Spinbox(root, from_=0, to=1)
        combo.pack()
        spin.pack()
        root.update_idletasks()
        root.update()
        combo.destroy()
        spin.destroy()
    finally:
        root.destroy()
    result["stage"] = "complete"
    save()
