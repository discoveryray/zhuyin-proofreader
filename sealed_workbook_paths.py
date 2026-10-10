"""Finite selected-project workbook bindings; absolute origins are never opened."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PureWindowsPath
import hashlib
import re
from functools import wraps

ROLES = {"actual": "01_實際注音", "candidate": "02_候選報告"}
LOCATION_CONTRACT = "sealed-workbook-location/1"
_bindings = ContextVar("sealed_workbook_bindings", default=None)


@contextmanager
def binding_scope():
    if _bindings.get() is not None:
        yield
        return
    token = _bindings.set({})
    try:
        yield
    finally:
        _bindings.reset(token)


def bound_operation(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        if args:
            trusted_root(args[0])
        with binding_scope():
            return operation(*args, **kwargs)
    return wrapped


def trusted_root(root):
    root = Path(root).absolute()
    if any(part.is_symlink() or part.is_junction() for part in (root, *root.parents)):
        raise ValueError("workbook binding selected root link/junction 不允許")
    return root


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def descriptor(info, kind):
    """Explicit legacy absolute-role adapter, or trusted two-part relative path."""
    if kind not in ROLES:
        raise ValueError("workbook binding 未知 role")
    value = info.get(kind + "_workbook")
    if not isinstance(value, str) or not value:
        raise ValueError("workbook binding 缺少 locator")
    path = PureWindowsPath(value)
    if ".." in path.parts or any(part in ("", ".") for part in value.replace("\\", "/").split("/") if part != ""):
        raise ValueError("workbook binding locator traversal 不允許")
    # An absolute historical origin is only a role/name descriptor. No drive
    # replacement and no filesystem query is ever performed on that origin.
    parts = path.parts[-2:] if path.is_absolute() or Path(value).is_absolute() else path.parts
    if (len(parts) != 2 or parts[0] != ROLES[kind] or not parts[1]
            or Path(parts[1]).suffix.lower() != ".xlsx" or ":" in parts[1]):
        raise ValueError("workbook binding locator 不在有限 role 目錄")
    return Path(*parts)


def owned_path(root, relative):
    root = trusted_root(root)
    relative = Path(relative)
    if (relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] not in ROLES.values()
            or any(p in ("..", ".") for p in relative.parts) or relative.suffix.lower() != ".xlsx"):
        raise ValueError("workbook binding relative path 不合法")
    target = root / relative
    for part in (root, target.parent, target):
        if part.is_symlink() or part.is_junction():
            raise ValueError("workbook binding link/junction 不允許")
    if target.resolve() != root.resolve() / relative:
        raise ValueError("workbook binding path 不屬於 selected project")
    if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
        raise ValueError("workbook binding 非獨立一般檔案")
    return target


def bind_workbooks(root, manifest, *, require_candidate=True):
    import standalone_proofread as sp
    sp.validate_manifest_integrity(manifest)
    root = Path(root).absolute()
    key = (str(root.resolve()), manifest["manifest_integrity_sha256"])
    cache = _bindings.get()
    frozen = dict(cache.get(key, {})) if cache is not None else {}
    result, identities, used = [], set(), set()
    role_files = {}
    for index, info in enumerate(manifest.get("pdfs", [])):
        pdf_sha = info.get("pdf_sha256")
        if not isinstance(pdf_sha, str) or re.fullmatch(r"[0-9a-f]{64}", pdf_sha) is None:
            raise ValueError("workbook binding 缺少封印來源 identity")
        for kind in ("actual", "candidate") if require_candidate else ("actual",):
            trusted_relative = descriptor(info, kind)
            wanted = info.get(kind + "_workbook_sha256")
            identity = (pdf_sha, kind)
            if identity in identities or not isinstance(wanted, str) or re.fullmatch(r"[0-9a-f]{64}", wanted) is None:
                raise ValueError("workbook binding 來源/role 重複或封印 SHA 不合法")
            identities.add(identity)
            if (index, kind) in frozen:
                relative, old_sha = frozen[index, kind]
                path = owned_path(root, relative)
                if old_sha != wanted or not path.is_file() or _digest(path) != wanted:
                    raise ValueError("workbook binding frozen path SHA 已變動；未重新定位")
                matches = [relative]
            else:
                if kind not in role_files:
                    directory = root / ROLES[kind]
                    owned_path(root, Path(ROLES[kind]) / "binding-check.xlsx")
                    role_files[kind] = [(Path(ROLES[kind]) / candidate.name,
                                        _digest(owned_path(root, Path(ROLES[kind]) / candidate.name)))
                                       for candidate in directory.glob("*.xlsx")] if directory.is_dir() else []
                matches = [relative for relative, digest in role_files[kind] if digest == wanted]
            if len(matches) != 1 or str(matches[0]).casefold() in used or matches[0] != trusted_relative:
                raise ValueError("DATA_INTEGRITY_ERROR：workbook binding 遺失、SHA 不符或定位不唯一")
            used.add(str(matches[0]).casefold())
            result.append((index, kind, matches[0], wanted))
            frozen[index, kind] = (matches[0], wanted)
    result = tuple(result)
    if cache is not None:
        cache[key] = frozen
    return result


def workbook_path(root, manifest, info, kind):
    import standalone_proofread as sp
    sp.validate_manifest_integrity(manifest)
    matches = [i for i, entry in enumerate(manifest.get("pdfs", [])) if entry == info]
    if len(matches) != 1:
        raise ValueError("workbook binding source metadata 不唯一")
    cache = _bindings.get()
    key = (str(Path(root).resolve()), manifest["manifest_integrity_sha256"])
    if cache is None:
        rows = bind_workbooks(root, manifest)
        relative, wanted = next((relative, sha) for index, role, relative, sha in rows if index == matches[0] and role == kind)
    else:
        if (matches[0], kind) not in cache.get(key, {}):
            bind_workbooks(root, manifest)
        relative, wanted = cache[key][matches[0], kind]
    path = owned_path(root, relative)
    if not path.is_file() or _digest(path) != wanted:
        raise ValueError("workbook binding frozen path SHA 已變動；未重新定位")
    return path


def remember_snapshot(root, manifest, expected):
    """Install authenticated recorded locations after backup restoration."""
    cache = _bindings.get()
    if cache is None:
        raise ValueError("workbook recovery 缺少 frozen binding scope")
    key = (str(Path(root).resolve()), manifest["manifest_integrity_sha256"])
    cache[key] = {(index // 2, ("actual", "candidate")[index % 2]): (Path(relative), wanted)
                  for index, (path, relative, wanted) in enumerate(expected)}


def snapshot_entries(root, manifest, snapshot):
    """Validate sealed recorded relatives without current-byte discovery."""
    contract = snapshot.get("location_contract")
    if "location_contract" in snapshot and contract != LOCATION_CONTRACT:
        raise ValueError("workbook snapshot 未知 location contract")
    entries = snapshot.get("entries")
    expected = [(info, kind) for info in manifest.get("pdfs", []) for kind in ROLES]
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError("workbook snapshot 備份數量不符")
    result, used, identities = [], set(), set()
    for entry, (info, kind) in zip(entries, expected):
        old_relative = descriptor(info, kind)
        identity = (info.get("pdf_sha256"), kind)
        if (not isinstance(identity[0], str) or re.fullmatch(r"[0-9a-f]{64}", identity[0]) is None
                or identity in identities):
            raise ValueError("workbook snapshot 來源 identity 不合法或重複")
        identities.add(identity)
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError("workbook snapshot entry contract 不符")
        relative = Path(entry["path"])
        if (relative.parent != Path(ROLES[kind]) or relative != old_relative
                or entry["sha256"] != info.get(kind + "_workbook_sha256")
                or not isinstance(entry["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
            raise ValueError("workbook snapshot role/source/relative 不符")
        path = owned_path(root, relative)
        if str(relative).casefold() in used:
            raise ValueError("workbook snapshot relative 重複")
        used.add(str(relative).casefold())
        result.append((path, str(relative), entry["sha256"]))
    return result
