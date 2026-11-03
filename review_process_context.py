"""Local, console-free spawn for the two review background-process owners.

Windows support is deliberately limited to the project's CPython 3.13.0
contract. Other platforms retain the standard spawn context. No global spawn
executable, interpreter state or Win32 API is modified.

The Windows Popen initializer below is adapted from CPython v3.13.0,
Lib/multiprocessing/popen_spawn_win32.py, Copyright Python Software Foundation.
Its only creation-policy change is CREATE_NO_WINDOW. The complete runtime
LICENSE text, including its bundled notices, is retained in
docs/licenses/CPython-3.13.0-LICENSE.txt with whitespace-only normalization.
The original bytes are preserved in the task evidence.
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def _require_windows_contract():
    if sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 13, 0):
        raise RuntimeError('Review background spawn requires Windows CPython 3.13.0; unsupported interpreter contract')


if sys.platform == 'win32':
    import msvcrt
    import _winapi
    from subprocess import CREATE_NO_WINDOW, STARTUPINFO, STARTF_FORCEOFFFEEDBACK
    from multiprocessing import popen_spawn_win32 as _winspawn
    from multiprocessing.context import SpawnContext, SpawnProcess

    class _NoConsoleSpawnPopen(_winspawn.Popen):
        """Retain stdlib handle, venv, serialization and lifecycle semantics."""

        def __init__(self, process_obj):
            prep_data = _winspawn.spawn.get_preparation_data(process_obj._name)
            rhandle, whandle = _winapi.CreatePipe(None, 0)
            wfd = msvcrt.open_osfhandle(whandle, 0)
            cmd = _winspawn.spawn.get_command_line(parent_pid=os.getpid(),
                                                  pipe_handle=rhandle)
            python_exe = _winspawn.spawn.get_executable()

            # CPython's redirector bypass preserves the venv through this env
            # variable. Keep it even when pythonw maps to a console base exe.
            if _winspawn.WINENV and _winspawn._path_eq(python_exe, sys.executable):
                cmd[0] = python_exe = sys._base_executable
                env = os.environ.copy()
                env['__PYVENV_LAUNCHER__'] = sys.executable
            else:
                env = None

            cmd = ' '.join('"%s"' % x for x in cmd)
            with open(wfd, 'wb', closefd=True) as to_child:
                try:
                    hp, ht, pid, tid = _winapi.CreateProcess(
                        python_exe, cmd, None, None, False, CREATE_NO_WINDOW,
                        env, None, STARTUPINFO(dwFlags=STARTF_FORCEOFFFEEDBACK))
                    _winapi.CloseHandle(ht)
                except:
                    _winapi.CloseHandle(rhandle)
                    raise

                self.pid = pid
                self.returncode = None
                self._handle = hp
                self.sentinel = int(hp)
                self.finalizer = _winspawn.util.Finalize(
                    self, _winspawn._close_handles, (self.sentinel, int(rhandle)))
                _winspawn.set_spawning_popen(self)
                try:
                    _winspawn.reduction.dump(prep_data, to_child)
                    _winspawn.reduction.dump(process_obj, to_child)
                finally:
                    _winspawn.set_spawning_popen(None)

    class _NoConsoleSpawnProcess(SpawnProcess):
        @staticmethod
        def _Popen(process_obj):
            return _NoConsoleSpawnPopen(process_obj)

    class _NoConsoleSpawnContext(SpawnContext):
        Process = _NoConsoleSpawnProcess


def review_spawn_context():
    """The review owners' local adapter; unsupported Windows fails closed."""
    if sys.platform != 'win32':
        return multiprocessing.get_context('spawn')
    _require_windows_contract()
    return _NoConsoleSpawnContext()
