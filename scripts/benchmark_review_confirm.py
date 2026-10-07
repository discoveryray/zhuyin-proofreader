"""Retired native Tk benchmark; retained pure historical summary helpers.

Synthetic contract-valid session/PDF only: no decoder or real-book acceptance
claim. Run this same script with --repository pointing at each revision. Stage
timings are inclusive (nested stages must not be summed); heartbeat gaps are
measured separately from processing and the unchanged 0.5 s cooldown.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, nullcontext
import ctypes
import functools
import importlib.metadata
import hashlib
import json
import math
import platform
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def summarize(values):
    ordered = sorted(values)
    return {"n": len(ordered), "median_ms": statistics.median(ordered) * 1000,
            "p95_ms": ordered[max(0, math.ceil(len(ordered) * .95) - 1)] * 1000}


def main():
    raise SystemExit("真視窗驗證已依政策取消; native Tk benchmark is retired")


def digest_manifest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def source_manifest(repository):
    # Root application sources plus the imported fixture constructor. A measured
    # uncommitted candidate is later bound to its commit by these exact bytes.
    paths = sorted(repository.glob("*.py")) + [repository / "tests/test_manual_review_usability_v580.py"]
    return {path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


@functools.lru_cache(maxsize=1)
def _windows_memory_api():
    # ctypes caches POINTER types. Define the structure and API exactly once;
    # defining them per heartbeat would make the measuring tool retain memory.
    if sys.platform != "win32":
        raise RuntimeError("This benchmark requires Windows GetProcessMemoryInfo")
    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return psapi.GetProcessMemoryInfo, kernel.GetCurrentProcess(), ProcessMemoryCountersEx


def memory_bytes():
    """OS process memory, including native Tk/PyMuPDF allocations (Windows)."""
    get_memory, process, counter_type = _windows_memory_api()
    counters = counter_type()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(process, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_bytes": counters.WorkingSetSize, "private_bytes": counters.PrivateUsage,
            "process_peak_working_set_bytes": counters.PeakWorkingSetSize}


def run_dialog_cycles(window, root, output, args, gui, sp, pump_until):
    raise RuntimeError("真視窗驗證已依政策取消; native dialog benchmark is retired")


if __name__ == "__main__":
    main()
