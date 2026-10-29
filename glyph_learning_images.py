"""Immutable learning pictures and a separate, bounded disposable cache.

These bytes document what a user saw; they are not a source of glyph truth.
No PDF is copied, and cache eviction never traverses the evidence namespace.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import zlib


DEFAULT_CACHE_LIMIT_BYTES = 256 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_SHA = re.compile(r"[0-9a-f]{64}")


def learning_data_root() -> Path:
    value = os.environ.get("LOCALAPPDATA", "")
    if not value or not Path(value).is_absolute():
        raise ValueError("LOCALAPPDATA 必須是已知絕對路徑；未使用目前目錄保存學習資料")
    return Path(value) / "DiscoveryRay" / "ZhuyinProofreader" / "GlyphLearning"


def _plain_path(path: Path) -> Path:
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("學習資料路徑不允許 link/junction")
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("學習資料必須是獨立一般檔案")
    return path


@contextmanager
def _storage_lock(root: Path, *, timeout: float = 2.0):
    root = _plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = _plain_path(root / ".learning-storage.lock")
    # OS locks are released on process exit. The retained lock file is never
    # mistaken for ownership or deleted while another process can hold it.
    with lock.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        started = time.monotonic()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("學習資料正在保存，請稍後重試") from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, raw: bytes):
    path = _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".learning-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _plain_path(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _png_bytes(raw: bytes) -> bytes:
    if not isinstance(raw, bytes) or not raw.startswith(_PNG) or len(raw) > 64 * 1024 * 1024:
        raise ValueError("確認圖片不是有效 PNG 或超過單張容量限制")
    offset, names = 8, []
    while offset + 12 <= len(raw):
        size = struct.unpack(">I", raw[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(raw):
            raise ValueError("確認圖片 PNG 截斷")
        name = raw[offset + 4:offset + 8]
        content = raw[offset + 8:offset + 8 + size]
        if zlib.crc32(name + content) & 0xffffffff != struct.unpack(">I", raw[end - 4:end])[0]:
            raise ValueError("確認圖片 PNG checksum 不符")
        if not names:
            if name != b"IHDR" or size != 13:
                raise ValueError("確認圖片 PNG 缺少 header")
            width, height = struct.unpack(">II", content[:8])
            if not 0 < width <= 20000 or not 0 < height <= 20000 or width * height > 40000000:
                raise ValueError("確認圖片 PNG 尺寸不合理")
        elif name == b"IHDR":
            raise ValueError("確認圖片 PNG 重複 header")
        names.append(name)
        offset = end
        if name == b"IEND":
            if size or offset != len(raw) or b"IDAT" not in names:
                raise ValueError("確認圖片 PNG 結尾無效")
            return raw
    raise ValueError("確認圖片 PNG 缺少完整結尾")


class LearningImageStore:
    def __init__(self, root=None, *, cache_limit_bytes=DEFAULT_CACHE_LIMIT_BYTES):
        self.root = _plain_path(Path(root) if root is not None else learning_data_root() / "images")
        if type(cache_limit_bytes) is not int or cache_limit_bytes < 0:
            raise ValueError("cache_limit_bytes 必須為非負整數")
        self.cache_limit_bytes = cache_limit_bytes

    def _reference(self, occurrence_id, raw, directory):
        if not isinstance(occurrence_id, str) or not occurrence_id or occurrence_id.strip() != occurrence_id:
            raise ValueError("確認圖片缺少 occurrence_id")
        digest = hashlib.sha256(_png_bytes(raw)).hexdigest()
        return {"occurrence_id": occurrence_id, "sha256": digest,
                "relative_path": f"{directory}/{digest}.png"}

    def _target(self, reference, directory):
        if not isinstance(reference, dict) or set(reference) != {"occurrence_id", "sha256", "relative_path"}:
            raise ValueError("確認圖片 reference 欄位不符")
        occurrence = reference["occurrence_id"]
        digest = reference["sha256"]
        if (not isinstance(occurrence, str) or not occurrence or occurrence.strip() != occurrence
                or not isinstance(digest, str) or _SHA.fullmatch(digest) is None
                or reference["relative_path"] != f"{directory}/{digest}.png"):
            raise ValueError("確認圖片 reference 路徑或 SHA 不符")
        return _plain_path(self.root / directory / f"{digest}.png")

    def save_evidence(self, occurrence_id: str, png_bytes: bytes) -> dict:
        reference = self._reference(occurrence_id, png_bytes, "evidence")
        with _storage_lock(self.root):
            target = self._target(reference, "evidence")
            if target.exists():
                if target.read_bytes() != png_bytes:
                    raise ValueError("確認圖片 evidence 內容已損毀；未覆寫")
            else:
                _atomic_write(target, png_bytes)
        return reference

    def read_evidence(self, reference: dict) -> bytes:
        target = self._target(reference, "evidence")
        raw = _png_bytes(target.read_bytes())
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("確認圖片 evidence SHA 不符")
        return raw

    def cache_png(self, occurrence_id: str, png_bytes: bytes) -> dict | None:
        reference = self._reference(occurrence_id, png_bytes, "cache")
        with _storage_lock(self.root):
            target = self._target(reference, "cache")
            if len(png_bytes) <= self.cache_limit_bytes:
                if target.exists() and target.read_bytes() != png_bytes:
                    raise ValueError("圖片 cache SHA 不符；未覆寫")
                if not target.exists():
                    _atomic_write(target, png_bytes)
                os.utime(target, None)
            self._evict_cache()
            return reference if target.exists() else None

    def read_cache(self, reference: dict) -> bytes | None:
        target = self._target(reference, "cache")
        if not target.exists():
            return None
        raw = _png_bytes(target.read_bytes())
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("圖片 cache SHA 不符")
        return raw

    def _evict_cache(self):
        directory = _plain_path(self.root / "cache")
        if not directory.exists():
            return
        files = []
        for path in directory.iterdir():
            _plain_path(path)
            if not path.is_file() or re.fullmatch(r"[0-9a-f]{64}\.png", path.name) is None:
                raise ValueError("cache 目錄有未知檔案；未刪除")
            stat = path.stat()
            files.append((stat.st_mtime_ns, path.name, stat.st_size, path))
        total = sum(row[2] for row in files)
        for _, _, size, path in sorted(files):
            if total <= self.cache_limit_bytes:
                break
            path.unlink()
            total -= size

    def trim_cache(self):
        with _storage_lock(self.root):
            self._evict_cache()


class PreparedLearningImages:
    """One worker's bounded, disposable pictures; never durable evidence.

    References reserve the eventual content-addressed evidence name. Losing
    this buffer invalidates the prepared action; confirmation cannot recreate
    the pixels or silently substitute a newly rendered image.
    """
    def __init__(self, limit_bytes=DEFAULT_CACHE_LIMIT_BYTES):
        if type(limit_bytes) is not int or limit_bytes < 0:
            raise ValueError("prepared image limit must be nonnegative")
        self.limit_bytes = limit_bytes
        self._pixels = {}

    @property
    def size_bytes(self):
        return sum(map(len, self._pixels.values()))

    def add(self, store, occurrence_id, raw):
        reference = store._reference(occurrence_id, raw, "evidence")
        digest = reference["sha256"]
        if digest not in self._pixels:
            if self.size_bytes + len(raw) > self.limit_bytes:
                raise ValueError("準備圖片超過容量限制；請減少本次樣本")
            self._pixels[digest] = raw
        return reference

    def read_evidence(self, reference):
        digest = reference.get("sha256")
        raw = self._pixels.get(digest)
        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("準備圖片已失效；請重新看圖")
        if reference != {"occurrence_id": reference.get("occurrence_id"), "sha256": digest,
                         "relative_path": f"evidence/{digest}.png"}:
            raise ValueError("準備圖片 reference 不符")
        return _png_bytes(raw)

    def persist_decision(self, store, references, publish, is_published):
        """Publish the decision while its new images remain storage-locked.

        Failure only rolls back files created here, and only when the caller
        proves that no durable decision references them. Existing shared
        evidence is never reclaimed by this operation.
        """
        pictures = [(reference, self.read_evidence(reference)) for reference in references]
        created = []
        with _storage_lock(store.root):
            try:
                for reference, raw in pictures:
                    if store._reference(reference["occurrence_id"], raw, "evidence") != reference:
                        raise ValueError("保存圖片與原準備內容不符")
                    target = store._target(reference, "evidence")
                    if target.exists():
                        if target.read_bytes() != raw:
                            raise ValueError("確認圖片 evidence 內容已損毀；未覆寫")
                    else:
                        # Track before atomic write: an exception may be raised
                        # after replacement has actually succeeded.
                        created.append((target, raw))
                        _atomic_write(target, raw)
                return publish()
            except Exception:
                try:
                    unpublished = is_published() is False
                except Exception:
                    unpublished = False
                if unpublished:
                    for target, raw in created:
                        if target.exists() and target.read_bytes() == raw:
                            target.unlink()
                raise

    def clear(self):
        self._pixels.clear()
