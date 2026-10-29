"""Isolated storage tests; no window and no claim of human image acceptance."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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
