"""Retired Tk automation, with pure historical resource-record parsing retained."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid


def select_resources(base_prefix, extension_path, tcl_version, tk_version, loaded_dlls):
    """Inspect only the interpreter installation's exact Windows resource paths."""
    base = Path(base_prefix).resolve(strict=True)
    extension = Path(extension_path).resolve(strict=True)
    if not extension.is_relative_to(base) or extension != (base / "DLLs" / "_tkinter.pyd").resolve(strict=True):
        raise ValueError("_tkinter is outside the interpreter installation")
    versions = {"tcl": tcl_version, "tk": tk_version}
    selected = {}
    for kind, version in versions.items():
        if re.fullmatch(r"[0-9]+\.[0-9]+", version) is None:
            raise ValueError("unsupported compiled Tcl/Tk version")
        dll_name = kind + version.replace(".", "") + "t.dll"
        expected_dll = base / "DLLs" / dll_name
        resource = base / "tcl" / (kind + version)
        script = resource / ("init.tcl" if kind == "tcl" else "tk.tcl")
        for path in (expected_dll, resource, script):
            if not path.resolve(strict=True).is_relative_to(base):
                raise ValueError("Tcl/Tk resource escapes interpreter installation")
        if Path(loaded_dlls[dll_name]).resolve(strict=True) != expected_dll.resolve(strict=True):
            raise ValueError("loaded Tcl/Tk DLL is outside the interpreter installation")
        payload = script.read_text(encoding="utf-8")
        pattern = (r"(?m)^package require -exact Tcl[ \t]+([0-9]+\.[0-9]+\.[0-9]+)\s*$" if kind == "tcl"
                   else r"(?m)^package require -exact Tk[ \t]+([0-9]+\.[0-9]+\.[0-9]+)\s*$")
        matches = re.findall(pattern, payload)
        if len(matches) != 1 or not matches[0].startswith(version + "."):
            raise ValueError("resource version differs from compiled Tcl/Tk version")
        selected[kind + "_library"] = str(resource.resolve(strict=True))
        selected[kind + "_version"] = matches[0]
        selected[kind + "_resource_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
        selected[kind + "_dll_sha256"] = hashlib.sha256(expected_dll.read_bytes()).hexdigest()
    return {"base_prefix": str(base), "interpreter": sys.executable,
            "extension": str(extension), "loaded_dlls": loaded_dlls, **selected}


def configuration():
    raise ValueError("真視窗驗證已依政策取消; Tcl/Tk configuration is retired")


def early_preflight(folder):
    raise ValueError("真視窗驗證已依政策取消; Tk capability/preflight is retired")


def main(argv=None):
    print("真視窗驗證已依政策取消; configure/preflight launchers are retired", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
