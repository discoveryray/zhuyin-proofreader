"""Real Windows hidden spawn and finite contract checks; no Tcl/Tk windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import traceback

import pytest

import review_process_context as context
import review_operation_worker as worker
import standalone_proofread as sp
from tests.test_review_process_operations import fixture, wait_preview


class _ForwardWinApi:
    """A module-local binding; never replace the shared native API functions."""

    def __init__(self, native, *, fail=False):
        self.native, self.fail = native, fail
        self.created, self.pipes, self.closed = [], [], []

    def __getattr__(self, name):
        return getattr(self.native, name)

    def CreatePipe(self, *args):
        pair = self.native.CreatePipe(*args)
        self.pipes.append(pair)
        return pair

    def CloseHandle(self, handle):
        self.closed.append(int(handle))
        return self.native.CloseHandle(handle)

    def CreateProcess(self, *args):
        # Refuse before forwarding; even a regressed test cannot create a
        # visible console to demonstrate the original bug.
        assert args[5] & subprocess.CREATE_NO_WINDOW, 'spawn lacks CREATE_NO_WINDOW'
        visible_env = ({key: args[6].get(key) for key in
                        ('__PYVENV_LAUNCHER__', 'ZHUYIN_NO_CONSOLE_TEST_NONCE')}
                       if args[6] is not None else None)
        self.created.append(dict(executable=args[0], command=args[1], flags=args[5],
                                 env=visible_env,
                                 inherit_handles=args[4], cwd=args[7]))
        if self.fail:
            raise OSError('isolated CreateProcess launch failure')
        return self.native.CreateProcess(*args)


def _runtime_child(connection, ready):
    try:
        assert ready.wait(10), 'parent Event was not delivered'
        console = ctypes.WinDLL('kernel32', use_last_error=True).GetConsoleWindow
        console.restype = wintypes.HWND
        connection.send(dict(pid=os.getpid(), executable=sys.executable,
            prefix=sys.prefix, base_prefix=sys.base_prefix,
            nonce=os.environ.get('ZHUYIN_NO_CONSOLE_TEST_NONCE'), console=int(console() or 0)))
    finally:
        connection.close()


def _identity():
    return dict(executable=sys.executable, base_executable=sys._base_executable,
                prefix=sys.prefix, base_prefix=sys.base_prefix,
                python_version=list(sys.version_info[:3]))


def _refuse_unguarded_standard_context(*args, **kwargs):
    raise AssertionError('review owner bypassed the local hidden-spawn factory')


def _assert_creation_runtime(created, identity):
    assert created['flags'] & subprocess.CREATE_NO_WINDOW
    assert created['inherit_handles'] is False and created['cwd'] is None
    if context._winspawn.WINENV:
        assert os.path.normcase(created['executable']) == os.path.normcase(identity['base_executable'])
        assert created['env']['__PYVENV_LAUNCHER__'] == identity['executable']
        assert created['env']['ZHUYIN_NO_CONSOLE_TEST_NONCE'] == 'isolated-hidden-spawn-nonce'


def test_local_hidden_spawn_keeps_runtime_pipe_event_and_global_state(tmp_path, monkeypatch):
    native, stdlib_process = context._winapi, multiprocessing.get_context('spawn').Process
    original_create, original_base = native.CreateProcess, sys._base_executable
    spy = _ForwardWinApi(native)
    monkeypatch.setattr(context, '_winapi', spy)
    monkeypatch.setenv('ZHUYIN_NO_CONSOLE_TEST_NONCE', 'isolated-hidden-spawn-nonce')
    local = context.review_spawn_context()
    assert local.Process is context._NoConsoleSpawnProcess
    parent, child = local.Pipe()
    ready = local.Event()
    process = local.Process(target=_runtime_child, args=(child, ready))
    started = False
    try:
        process.start()
        started = True
        child.close()
        ready.set()
        assert parent.poll(15), 'hidden child did not return its runtime identity'
        result = parent.recv()
        process.join(15)
        assert process.exitcode == 0 and not process.is_alive()
        assert result['pid'] != os.getpid() and result['console'] == 0
        assert result['executable'] == sys.executable
        assert result['prefix'] == sys.prefix and result['base_prefix'] == sys.base_prefix
        assert result['nonce'] == 'isolated-hidden-spawn-nonce'
        assert len(spy.created) == 1
        _assert_creation_runtime(spy.created[0], _identity())
        for name in ('wait', 'poll', 'terminate', 'kill', 'close', 'duplicate_for_child'):
            assert getattr(context._NoConsoleSpawnPopen, name) is getattr(context._winspawn.Popen, name)
        assert multiprocessing.get_context('spawn').Process is stdlib_process
        assert native.CreateProcess is original_create
        assert sys._base_executable == original_base
    finally:
        if started:
            if process.is_alive():
                process.terminate()
            process.join(15)
        parent.close()
        child.close()
        process.close()


def _run_pythonw_products(output, result_path):
    """Finite subprocess helper, called only behind a guarded __main__ entry."""
    result = dict(identity=_identity(), ok=False)
    native, original_process = context._winapi, multiprocessing.get_context('spawn').Process
    original_create, original_base = native.CreateProcess, sys._base_executable
    spy = _ForwardWinApi(native)
    context._winapi = spy
    original_worker_mp, original_factory = worker.multiprocessing, worker.review_spawn_context

    def guarded_factory():
        local = original_factory()
        assert local.Process is context._NoConsoleSpawnProcess
        return local

    # Guard the original regression before it could call the shared Win32 API.
    # These are only module bindings; shared multiprocessing remains unchanged.
    worker.multiprocessing = SimpleNamespace(get_context=_refuse_unguarded_standard_context)
    worker.review_spawn_context = guarded_factory
    service = job = None
    try:
        output = Path(output)
        service = worker.ReviewProcessService(output)
        snapshot = service.load_resume_snapshot()
        row = snapshot.ledger[0]
        progress = []
        event = sp.build_manual_expected_event(row, operation='ENTER_EXPECTED', expected_set='ㄎㄢ')
        saved = service.save_event(row['review_id'], event, expected_manifest=snapshot.manifest,
                                   expected_db=snapshot.db, progress=progress.append)
        assert saved.db['events'][row['review_id']]['expected_set'] == ['ㄎㄢ']
        assert saved.ledger[0]['actual'] == row['actual']
        assert progress and service.worker_pid != os.getpid()
        with pytest.raises(worker.ReviewOperationError, match='快照已過期') as error:
            service.invoke('validate_staging', {'snapshot_key': '0' * 64})
        result['service'] = dict(pid=service.worker_pid, result='SAVED',
                                 error=str(error.value), progress=progress)
        service.close()
        assert not service._process.is_alive() and service._connection.closed
        assert len(spy.created) == 1
        result['service']['creation'] = spy.created[0]

        from review_display import occurrence_preview
        pixels, target, notice = occurrence_preview(row, display_width=984)
        payload = dict(entry=row, padding=(150, 115), output_dir=str(output), display_width=984)
        job = worker.PreviewJob(payload)
        value = wait_preview(job)
        assert value['pid'] != os.getpid()
        assert value['target'] == target and value['notice'] == notice
        digest = hashlib.sha256(value['png']).hexdigest()
        assert digest == hashlib.sha256(pixels.tobytes('png')).hexdigest()
        invalid = dict(payload, entry=dict(row, pdf_sha256='0' * 64))
        job.submit(invalid)
        with pytest.raises(ValueError) as preview_error:
            wait_preview(job)
        job.cancel()
        job._cleanup.join(15)
        assert not job._cleanup.is_alive() and not job.process.is_alive()
        assert job.connection.closed
        assert len(spy.created) == 2
        result['preview'] = dict(pid=value['pid'], pixel_sha256=digest,
                                 error=str(preview_error.value), creation=spy.created[1])
        for created in spy.created:
            _assert_creation_runtime(created, result['identity'])
        assert native.CreateProcess is original_create
        assert sys._base_executable == original_base
        assert multiprocessing.get_context('spawn').Process is original_process
        result['ok'] = True
    except BaseException:
        result['error'] = traceback.format_exc()
        raise
    finally:
        if service is not None:
            service.close()
        if job is not None and not getattr(job, '_cleanup', None):
            job.cancel()
            job._cleanup.join(15)
        context._winapi = native
        worker.multiprocessing, worker.review_spawn_context = original_worker_mp, original_factory
        Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def test_pythonw_service_and_preview_use_hidden_spawn_with_real_results(tmp_path, monkeypatch):
    fixture(tmp_path)
    result_path = tmp_path / 'pythonw-products-result.json'
    script = tmp_path / 'pythonw-products-helper.py'
    script.write_text(
        'from tests.test_review_no_console_spawn_20261103 import _run_pythonw_products\n'
        'if __name__ == "__main__":\n'
        '    import sys\n'
        '    _run_pythonw_products(sys.argv[1], sys.argv[2])\n', encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root)
    env['ZHUYIN_NO_CONSOLE_TEST_NONCE'] = 'isolated-hidden-spawn-nonce'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    assert pythonw.is_file()
    completed = subprocess.run([str(pythonw), str(script), str(tmp_path), str(result_path)],
        cwd=root, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    assert result_path.is_file(), completed.stderr.decode('utf-8', errors='replace')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    assert completed.returncode == 0 and result['ok'], result.get('error', result)
    assert Path(result['identity']['executable']).name.lower() == 'pythonw.exe'
    if sys.prefix != sys.base_prefix:
        assert Path(result['identity']['base_executable']).name.lower() == 'python.exe'
        assert result['identity']['prefix'] != result['identity']['base_prefix']
    for owner in ('service', 'preview'):
        assert result[owner]['creation']['flags'] & subprocess.CREATE_NO_WINDOW
        assert result[owner]['error']
        if result[owner]['creation']['env'] is not None:
            assert result[owner]['creation']['env']['__PYVENV_LAUNCHER__'] == result['identity']['executable']


def _assert_invalid_handle(handle):
    get_information = ctypes.WinDLL('kernel32', use_last_error=True).GetHandleInformation
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    ctypes.set_last_error(0)
    assert not get_information(handle, ctypes.byref(flags))
    assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE


@pytest.mark.parametrize('owner', ['service', 'preview'])
def test_hidden_spawn_launch_failure_closes_pipes_and_keeps_owner_safe(tmp_path, monkeypatch, owner):
    _, row = fixture(tmp_path)
    spy = _ForwardWinApi(context._winapi, fail=True)
    monkeypatch.setattr(context, '_winapi', spy)
    local = context.review_spawn_context()
    assert local.Process is context._NoConsoleSpawnProcess
    connections = []
    pipe = local.Pipe

    def tracked_pipe(*args, **kwargs):
        pair = pipe(*args, **kwargs)
        connections.extend(pair)
        return pair

    monkeypatch.setattr(local, 'Pipe', tracked_pipe)
    monkeypatch.setattr(worker, 'review_spawn_context', lambda: local)
    monkeypatch.setattr(worker, 'multiprocessing',
                        SimpleNamespace(get_context=_refuse_unguarded_standard_context))
    service = worker.ReviewProcessService(tmp_path) if owner == 'service' else None
    with pytest.raises(OSError, match='isolated CreateProcess launch failure'):
        if service is not None:
            service.load_resume_snapshot()
        else:
            worker.PreviewJob(dict(entry=row, padding=(95, 60), output_dir=str(tmp_path), display_width=400))
    assert len(spy.created) == 1 and len(spy.pipes) == 1
    read_handle, write_handle = spy.pipes[0]
    assert int(read_handle) in spy.closed
    _assert_invalid_handle(read_handle)
    _assert_invalid_handle(write_handle)
    assert connections and all(connection.closed for connection in connections)
    assert context._winspawn.get_spawning_popen() is None
    if service is not None:
        assert service._process is None
        service.close()
        service.close()
        assert service._closed


@pytest.mark.parametrize(('implementation', 'version'), [
    ('cpython', (3, 13, 1)), ('pypy', (3, 13, 0)),
], ids=['future-version', 'non-cpython'])
def test_hidden_spawn_rejects_unknown_windows_contract_before_context_creation(monkeypatch, implementation, version):
    monkeypatch.setattr(context, 'sys', SimpleNamespace(platform='win32',
        implementation=SimpleNamespace(name=implementation), version_info=version))
    with pytest.raises(RuntimeError, match='CPython 3.13.0'):
        context.review_spawn_context()


def test_non_windows_factory_retains_standard_spawn_context(monkeypatch):
    standard = multiprocessing.get_context('spawn')
    monkeypatch.setattr(context, 'sys', SimpleNamespace(platform='linux'))
    assert context.review_spawn_context() is standard
