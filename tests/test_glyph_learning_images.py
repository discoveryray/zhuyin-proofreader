"""Isolated storage tests; no window and no claim of human image acceptance."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
import multiprocessing
from threading import Event, current_thread
import zlib

import pytest

from glyph_learning_images import LearningImageStore, _storage_lock, learning_data_root


def png(red=0):
    def chunk(name, value):
        return (struct.pack(">I", len(value)) + name + value
                + struct.pack(">I", zlib.crc32(name + value) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 2, 3]))) + chunk(b"IEND", b""))


def test_image_content_deduplicates_without_copying_source_pdf(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"PDF remains at its original referenced location")
    before = source.read_bytes()
    store = LearningImageStore(tmp_path / "persistent-images")
    raw = png()
    first = store.save_evidence("occ-first", raw)
    second = store.save_evidence("occ-second", raw)
    assert first["sha256"] == hashlib.sha256(raw).hexdigest()
    assert first["relative_path"] == second["relative_path"]
    assert first["occurrence_id"] != second["occurrence_id"]
    assert len(list((store.root / "evidence").iterdir())) == 1
    assert store.read_evidence(first) == raw
    assert not list(store.root.rglob("*.pdf"))
    assert source.read_bytes() == before


def test_cache_limit_never_evicts_persistent_evidence(tmp_path):
    raw = png(12)
    store = LearningImageStore(tmp_path / "pictures", cache_limit_bytes=len(raw))
    evidence = store.save_evidence("occ", raw)
    first = store.cache_png("one", raw)
    second = store.cache_png("two", png(29))
    assert second is not None
    assert store.read_cache(first) is None
    assert sum(path.stat().st_size for path in (store.root / "cache").iterdir()) <= len(raw)
    store.cache_limit_bytes = 0
    store.trim_cache()
    assert not list((store.root / "cache").iterdir())
    assert store.read_evidence(evidence) == raw
    assert LearningImageStore(tmp_path / "default").cache_limit_bytes == 268435456
    assert store.cache_png("oversize", raw) is None


def test_evidence_tampering_bad_images_and_path_escape_fail_closed(tmp_path):
    store = LearningImageStore(tmp_path / "pictures")
    reference = store.save_evidence("occ", png())
    for invalid in ({**reference, "relative_path": "../other.png"},
                    {**reference, "extra": "unknown"},
                    {**reference, "sha256": "A" * 64}):
        with pytest.raises(ValueError):
            store.read_evidence(invalid)
    with pytest.raises(ValueError):
        store.save_evidence("occ", png()[:-3])
    path = store.root / reference["relative_path"]
    path.write_bytes(png(8))
    with pytest.raises(ValueError, match="SHA"):
        store.read_evidence(reference)
    with pytest.raises(ValueError, match="損毀"):
        store.save_evidence("occ", png())
    assert path.read_bytes() == png(8)


def test_concurrent_save_and_process_exit_do_not_duplicate_or_leave_owned_lock(tmp_path):
    root = tmp_path / "pictures"
    store = LearningImageStore(root)
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda index: store.save_evidence(f"occ-{index}", png()), range(8)))
    assert len({item["sha256"] for item in refs}) == 1
    assert len(list((root / "evidence").iterdir())) == 1
    source = "from glyph_learning_images import _storage_lock; from pathlib import Path; import os,sys\nwith _storage_lock(Path(sys.argv[1])):\n os._exit(0)\n"
    subprocess.run([sys.executable, "-c", source, str(root)], check=True,
                   cwd=Path(__file__).resolve().parents[1])
    assert store.read_evidence(store.save_evidence("retry", png())) == png()


def test_lock_refusal_and_unknown_cache_file_preserve_all_bytes(tmp_path):
    store = LearningImageStore(tmp_path / "pictures")
    with _storage_lock(store.root):
        with pytest.raises(TimeoutError):
            with _storage_lock(store.root, timeout=0.02):
                raise AssertionError("second writer must not be admitted")
    ref = store.save_evidence("occ", png())
    cache = store.root / "cache"
    cache.mkdir()
    unknown = cache / "do-not-delete.txt"
    unknown.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="未知"):
        store.trim_cache()
    assert unknown.read_text(encoding="utf-8") == "keep"
    assert store.read_evidence(ref) == png()


def test_missing_appdata_never_falls_back_to_repository(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ValueError, match="LOCALAPPDATA"):
        learning_data_root()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert learning_data_root() == tmp_path / "DiscoveryRay" / "ZhuyinProofreader" / "GlyphLearning"


def _os_byte_zero(handle, *, unlock=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def _raw_empty_range_holder(path):
    # Lock byte 0 past EOF using the documented OS primitive, without sentinel I/O.
    with path.open("a+b") as handle:
        _os_byte_zero(handle)
        try:
            yield
        finally:
            _os_byte_zero(handle, unlock=True)


def test_empty_lock_first_writer_waits_without_unlocked_initialization(tmp_path, monkeypatch):
    root = tmp_path / "pictures"
    root.mkdir()
    lock_path = root / ".learning-storage.lock"
    positioned, continue_writer, admitted = Event(), Event(), Event()
    original_open = Path.open

    class PositionedHandle:
        """Delay only positioning; all I/O and OS locking remain real."""
        def __init__(self, handle):
            self.handle = handle
            self.first_seek = True

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def seek(self, *args):
            result = self.handle.seek(*args)
            if self.first_seek:
                self.first_seek = False
                positioned.set()
                assert continue_writer.wait(5), "holder did not release positioning gate"
            return result

    def scheduled_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == lock_path and current_thread().name.startswith("late-image-lock"):
            return PositionedHandle(handle)
        return handle

    def late_writer():
        with _storage_lock(root):
            admitted.set()

    monkeypatch.setattr(Path, "open", scheduled_open)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-image-lock") as pool:
        pending = pool.submit(late_writer)
        try:
            assert positioned.wait(5), "late writer never opened the fresh lock"
            assert lock_path.read_bytes() == b""
            with _raw_empty_range_holder(lock_path):
                continue_writer.set()
                with pytest.raises(FutureTimeoutError):
                    pending.result(timeout=0.1)
                assert not admitted.is_set()
            pending.result(timeout=5)
            assert admitted.is_set()
            assert lock_path.read_bytes() == b""
        finally:
            continue_writer.set()


@pytest.mark.parametrize("initial", [b"", b"0"], ids=["empty", "existing"])
def test_os_byte_zero_excludes_independent_handle_without_changing_lock_bytes(tmp_path, initial):
    root = tmp_path / "pictures"
    root.mkdir()
    lock_path = root / ".learning-storage.lock"
    lock_path.write_bytes(initial)
    identity = lock_path.stat().st_ino
    with lock_path.open("a+b") as other:
        with _storage_lock(root):
            assert lock_path.stat().st_size == len(initial)
            with pytest.raises(OSError):
                _os_byte_zero(other)
        _os_byte_zero(other)
        _os_byte_zero(other, unlock=True)
    assert lock_path.read_bytes() == initial
    assert lock_path.stat().st_ino == identity


def _hold_picture_lock(root, ready, release, abrupt):
    with _storage_lock(Path(root)):
        ready.set()
        if not release.wait(10):
            raise AssertionError("parent did not release isolated holder")
        if abrupt:
            os._exit(0)


@pytest.mark.parametrize("abrupt", [False, True], ids=["normal-unlock", "process-exit"])
def test_process_held_empty_lock_times_out_without_touching_images_then_reenters(tmp_path, abrupt):
    store = LearningImageStore(tmp_path / "pictures", cache_limit_bytes=len(png()))
    original = store.save_evidence("shared", png())
    cached = store.cache_png("cache", png())
    lock_path = store.root / ".learning-storage.lock"
    assert lock_path.read_bytes() == b""
    identity = lock_path.stat().st_ino
    before = {p.relative_to(store.root).as_posix(): p.read_bytes()
              for p in store.root.rglob("*") if p.is_file() and p != lock_path}
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_picture_lock,
                             args=(str(store.root), ready, release, abrupt))
    holder.start()
    try:
        assert ready.wait(10), "child never acquired the real empty-file lock"
        with pytest.raises(TimeoutError, match="正在保存"):
            store.save_evidence("blocked", png(39))
        assert {p.relative_to(store.root).as_posix(): p.read_bytes()
                for p in store.root.rglob("*") if p.is_file() and p != lock_path} == before
        assert lock_path.stat().st_size == 0
        assert store.read_evidence(original) == png()
        assert store.read_cache(cached) == png()
        release.set()
        holder.join(timeout=10)
        assert holder.exitcode == 0
        assert store.save_evidence("after-exit", png())["relative_path"] == original["relative_path"]
        assert store.read_evidence(store.save_evidence("new-after-exit", png(39))) == png(39)
        assert len(list((store.root / "evidence").iterdir())) == 2
        assert lock_path.read_bytes() == b""
        assert lock_path.stat().st_ino == identity
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
        holder.close()


def _save_shared_picture(root, index, start, results):
    start.wait(timeout=10)
    reference = LearningImageStore(Path(root)).save_evidence(f"process-{index}", png())
    results.put(reference)


@pytest.mark.parametrize("initial", [None, b"0"], ids=["fresh", "existing"])
def test_barrier_started_process_saves_share_one_immutable_image(tmp_path, initial):
    root = tmp_path / "pictures"
    root.mkdir()
    lock_path = root / ".learning-storage.lock"
    if initial is not None:
        lock_path.write_bytes(initial)
    context = multiprocessing.get_context("spawn")
    start, results = context.Barrier(3), context.Queue()
    writers = [context.Process(target=_save_shared_picture,
                               args=(str(root), index, start, results)) for index in range(3)]
    for writer in writers:
        writer.start()
    try:
        for writer in writers:
            writer.join(timeout=10)
            assert writer.exitcode == 0
        references = [results.get(timeout=5) for _ in writers]
        assert len({r["occurrence_id"] for r in references}) == 3
        assert len({r["relative_path"] for r in references}) == 1
        assert len(list((root / "evidence").iterdir())) == 1
        assert all(LearningImageStore(root).read_evidence(r) == png() for r in references)
        assert lock_path.read_bytes() == (b"" if initial is None else initial)
    finally:
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=5)
            writer.close()
        results.close()
        results.join_thread()


def test_exception_in_critical_section_unlocks_same_empty_file(tmp_path):
    root = tmp_path / "pictures"
    lock_path = root / ".learning-storage.lock"
    with pytest.raises(RuntimeError, match="isolated critical failure"):
        with _storage_lock(root):
            assert lock_path.stat().st_size == 0
            identity = lock_path.stat().st_ino
            raise RuntimeError("isolated critical failure")
    store = LearningImageStore(root)
    assert store.read_evidence(store.save_evidence("after-error", png())) == png()
    assert lock_path.read_bytes() == b""
    assert lock_path.stat().st_ino == identity
