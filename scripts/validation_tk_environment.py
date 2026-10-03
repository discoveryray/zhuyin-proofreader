"""Bounded same-installation Tcl/Tk wiring; job-local, with retained early probe."""
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
    import _tkinter
    if sys.platform != "win32" or sys.version_info[:3] != (3, 13, 0):
        raise ValueError("Tcl/Tk validation wiring requires Windows Python 3.13.0")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint
    loaded = {}
    for kind, version in (("tcl", _tkinter.TCL_VERSION), ("tk", _tkinter.TK_VERSION)):
        name = kind + version.replace(".", "") + "t.dll"
        handle = kernel.GetModuleHandleW(name)
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetModuleFileNameW(handle, buffer, len(buffer)) if handle else 0
        if not length or length >= len(buffer):
            raise ValueError("cannot establish actual loaded module path: " + name)
        loaded[name] = buffer.value
    return select_resources(sys.base_prefix, _tkinter.__file__, _tkinter.TCL_VERSION,
                            _tkinter.TK_VERSION, loaded)


def early_preflight(folder):
    from scripts import validation_runner as runner
    env = runner.environment()
    runner.validate_environment(env)
    temporary = folder / "temp"
    temporary.mkdir()
    child_env = os.environ.copy()
    child_env.update(VALIDATION_PREFLIGHT_OUTPUT=str(folder / "preflight.json"),
                     VALIDATION_TEMP=str(temporary))
    command = [sys.executable, "-m", "pytest", str(runner.ROOT / "scripts/gui_preflight.py"),
               "-q", "-rs", "--capture=fd", f"--junitxml={folder / 'preflight.xml'}",
               f"--basetemp={temporary / 'preflight'}"]
    record = {"schema": "zhuyin-early-preflight/1", "kind": "early_preflight", "started_at": runner.utc_now(), "ci": {name: os.environ.get(name) for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME")},
              "source_sha256": {str(path.relative_to(runner.ROOT)): runner.digest(path) for path in (Path(__file__).resolve(), runner.ROOT / "scripts/gui_preflight.py")},
              "candidate": runner.snapshot(), "environment": env, "command": command,
              "timeout_seconds": 60}
    (folder / "preflight-start.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    result = runner.run_process(command, folder, "preflight.log", 60, child_env)
    record.update(result)
    if result["outcome"] == "success":
        probe = json.loads((folder / "preflight.json").read_text(encoding="utf-8"))
        if probe.get("stage") != "complete":
            record.update(outcome="failed", error="preflight did not complete")
    record["artifacts"] = {name: runner.digest(folder / name) for name in ("preflight.log", "preflight.json", "preflight.xml") if (folder / name).is_file()}
    (folder / "preflight-result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record["outcome"] == "success"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "preflight"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args(argv)
    folder = args.evidence_root.resolve() / ("tk-" + args.command + "-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    result = {"outcome": "failed", "command": sys.argv, "interpreter": sys.executable,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        if args.command == "preflight":
            # A direct script invocation needs the repository package import path.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            return 0 if early_preflight(folder) else 2
        if args.github_env is None or str(args.github_env) != os.environ.get("GITHUB_ENV"):
            raise ValueError("configuration only writes the current job GITHUB_ENV")
        result.update(configuration())
        values = (("TCL_LIBRARY", result["tcl_library"]), ("TK_LIBRARY", result["tk_library"]))
        if any("\n" in value or "\r" in value for _, value in values):
            raise ValueError("invalid Tcl/Tk path for job environment")
        with args.github_env.open("a", encoding="utf-8", newline="\n") as handle:
            for key, value in values:
                handle.write(key + "=" + value + "\n")
        result["outcome"] = "success"
        return 0
    except (ValueError, OSError, KeyError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        print(result["error"], file=sys.stderr)
        return 2
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        if args.command == "configure" or "error" in result:
            (folder / "configuration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
