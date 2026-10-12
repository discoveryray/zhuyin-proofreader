"""Read-only, synthetic real-PDF regressions for complete exact-SHA batches."""

import copy
import hashlib
from pathlib import Path

import pytest

import pdf_portability as portable
from tests.test_pdf_portability import pdf


def batch(root, prefix, *, texts=("same lesson", "same lesson")):
    root.mkdir(exist_ok=True)
    infos, evidence = [], []
    for index, text in enumerate(texts):
        path = root / f"{prefix}-{index}.pdf"
        pdf(path, title=f"{prefix} distinct bytes {index}", text=text)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        infos.append({"pdf": str(path), "pdf_name": path.name, "pdf_sha256": sha})
        evidence.append({"pdf_name": path.name, "pdf_sha256": sha,
                         "pages": portable._page_signatures(path)})
    return {"pdfs": infos}, {"pdfs": evidence}


@pytest.mark.parametrize("source_reversed", [False, True])
@pytest.mark.parametrize("target_reversed", [False, True])
def test_complete_same_sha_batch_disambiguates_identical_pages(
        tmp_path, source_reversed, target_reversed):
    manifest, proof = batch(tmp_path, "exact")
    shas = [info["pdf_sha256"] for info in manifest["pdfs"]]
    assert len(set(shas)) == 2
    assert proof["pdfs"][0]["pages"] == proof["pdfs"][1]["pages"]
    target = copy.deepcopy(manifest)
    # Names and array positions are presentation metadata, never the selector.
    for info in target["pdfs"]:
        info["pdf_name"] = "same displayed name.pdf"
    if source_reversed:
        proof["pdfs"].reverse()
    if target_reversed:
        target["pdfs"].reverse()
    before = {info["pdf"]: hashlib.sha256(Path(info["pdf"]).read_bytes()).hexdigest()
              for info in manifest["pdfs"]}
    mapping, geometry = portable._match_pdfs(proof, manifest, target, tmp_path)
    assert mapping == {shas[0]: shas[0], shas[1]: shas[1]}
    for carried in proof["pdfs"]:
        source_pages, target_pages = geometry[carried["pdf_sha256"]]
        assert source_pages is carried["pages"]
        assert target_pages == carried["pages"]
    assert {info["pdf"]: hashlib.sha256(Path(info["pdf"]).read_bytes()).hexdigest()
            for info in manifest["pdfs"]} == before


@pytest.mark.parametrize("side", ["source", "target"])
def test_duplicate_sha_batch_is_rejected_without_overwriting_mapping(tmp_path, side):
    manifest, proof = batch(tmp_path, "duplicates")
    target = copy.deepcopy(manifest)
    if side == "source":
        proof["pdfs"][1] = copy.deepcopy(proof["pdfs"][0])
    else:
        target["pdfs"][1] = copy.deepcopy(target["pdfs"][0])
    with pytest.raises(ValueError, match="SHA 重複"):
        portable._match_pdfs(proof, manifest, target, tmp_path)


@pytest.mark.parametrize("field", ["page_count", "width", "height", "pixels_width",
                                   "pixels_height", "rgb_sha256", "candidate_text_sha256"])
def test_exact_sha_batch_still_rejects_changed_carried_pages(tmp_path, field):
    manifest, proof = batch(tmp_path, "tampered")
    if field == "page_count":
        proof["pdfs"][0]["pages"] = []
    else:
        page = proof["pdfs"][0]["pages"][0]
        page[field] = (page[field] + 1 if isinstance(page[field], (int, float)) else "f" * 64)
    with pytest.raises(ValueError, match="同 SHA PDF.*不符"):
        portable._match_pdfs(proof, manifest, manifest, tmp_path)


def test_same_sha_page_mismatch_never_falls_back_to_other_pdf(tmp_path):
    manifest, proof = batch(tmp_path, "swapped", texts=("first lesson", "second lesson"))
    # Old visual matching could pair both swapped proofs to the opposite SHA.
    proof["pdfs"][0]["pages"], proof["pdfs"][1]["pages"] = (
        proof["pdfs"][1]["pages"], proof["pdfs"][0]["pages"])
    with pytest.raises(ValueError, match="同 SHA PDF.*不符"):
        portable._match_pdfs(proof, manifest, manifest, tmp_path)


def test_exact_sha_batch_checks_physical_target_bytes(tmp_path):
    manifest, proof = batch(tmp_path, "physical")
    first, second = [Path(info["pdf"]) for info in manifest["pdfs"]]
    first.write_bytes(second.read_bytes())
    before = first.read_bytes()
    with pytest.raises((ValueError, FileNotFoundError), match="SHA|bytes|來源|本地|內容"):
        portable._match_pdfs(proof, manifest, manifest, tmp_path)
    assert first.read_bytes() == before


def test_different_sha_sets_keep_unique_content_mapping(tmp_path):
    source, proof = batch(tmp_path / "source", "a", texts=("first lesson", "second lesson"))
    target, _ = batch(tmp_path / "target", "b", texts=("second lesson", "first lesson"))
    mapping, geometry = portable._match_pdfs(proof, source, target, tmp_path / "target")
    assert mapping == {source["pdfs"][0]["pdf_sha256"]: target["pdfs"][1]["pdf_sha256"],
                       source["pdfs"][1]["pdf_sha256"]: target["pdfs"][0]["pdf_sha256"]}
    assert set(geometry) == set(mapping)


def test_different_sha_sets_keep_ambiguous_content_rejection(tmp_path):
    source, proof = batch(tmp_path / "source", "a")
    target, _ = batch(tmp_path / "target", "b")
    with pytest.raises(ValueError, match="無法唯一對應"):
        portable._match_pdfs(proof, source, target, tmp_path / "target")


@pytest.mark.parametrize("legacy_first", [False, True])
def test_missing_text_batch_retains_order_sensitive_legacy_adapter(tmp_path, legacy_first):
    manifest, proof = batch(tmp_path, "legacy")
    legacy = proof["pdfs"][1]
    for page in legacy["pages"]:
        del page["candidate_text_sha256"]
    if not legacy_first:
        # Complete source is ambiguous until the legacy exact-byte adapter has
        # selected its single target. Do not grant this batch the new route.
        with pytest.raises(ValueError, match="無法唯一對應"):
            portable._match_pdfs(proof, manifest, manifest, tmp_path)
    else:
        proof["pdfs"].reverse()
        mapping, geometry = portable._match_pdfs(proof, manifest, manifest, tmp_path)
        assert mapping == {info["pdf_sha256"]: info["pdf_sha256"] for info in manifest["pdfs"]}
        source_pages, target_pages = geometry[legacy["pdf_sha256"]]
        assert source_pages is target_pages
        assert source_pages is not legacy["pages"]
        assert "candidate_text_sha256" in source_pages[0]


def test_legacy_missing_text_different_bytes_remain_rejected(tmp_path):
    source, proof = batch(tmp_path / "source", "a", texts=("same lesson",))
    target, _ = batch(tmp_path / "target", "b", texts=("same lesson",))
    del proof["pdfs"][0]["pages"][0]["candidate_text_sha256"]
    with pytest.raises(ValueError, match="缺少校對文字層"):
        portable._match_pdfs(proof, source, target, tmp_path / "target")


def test_pdf_count_mismatch_remains_rejected(tmp_path):
    manifest, proof = batch(tmp_path, "count")
    proof["pdfs"].pop()
    with pytest.raises(ValueError, match="PDF 數量不同"):
        portable._match_pdfs(proof, manifest, manifest, tmp_path)
