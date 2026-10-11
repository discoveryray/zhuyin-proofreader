"""Build a candidate ZIP exclusively from an exact, clean Git commit.

No user data, DB, imports of product modules or dependency installation occurs.
The checked-in finite allowlist and seed pins are read from that same commit.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ALLOWLIST = "release/package_allowlist.json"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("invalid package path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("invalid package path: " + value)
    return value


def prepare(repo, commit):
    repo = Path(repo).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source commit must be an exact 40-character SHA")
    if git(repo, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("checkout HEAD does not match source commit")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("source checkout must be clean")
    tree = git(repo, "rev-parse", commit + "^{tree}").decode().strip()
    objects = {}
    for raw in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if raw:
            header, path = raw.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            objects[path.decode("utf-8")] = (mode, kind, oid)

    def blob(path):
        entry = objects.get(path)
        if not entry or entry[0] not in {"100644", "100755"} or entry[1] != "blob":
            raise ValueError("missing or non-regular source: " + path)
        return git(repo, "cat-file", "blob", entry[2])

    spec = json.loads(blob(ALLOWLIST))
    if spec.get("schema") != "zhuyin-release-allowlist/1" or spec.get("version") != "5.8.0":
        raise ValueError("unsupported allowlist contract")
    entries = spec.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty allowlist")
    payloads, roles = {}, {}
    folded = set()
    for entry in entries:
        path = safe_path(entry["path"])
        if path.casefold() in folded or path == "package-manifest.json":
            raise ValueError("duplicate or reserved package path: " + path)
        folded.add(path.casefold())
        role = entry["role"]
        if role not in {"runtime_module", "runtime_asset", "builtin_seed", "entrypoint", "document", "dependency_lock", "metadata"}:
            raise ValueError("unknown package role: " + role)
        raw = blob(path)
        if role == "builtin_seed" and digest(raw) != entry.get("baseline_sha256"):
            raise ValueError("built-in seed changed without classification: " + path)
        payloads[path], roles[path] = raw, role
    manifest = json.loads(payloads["runtime_asset_manifest.json"])
    if manifest["tool_version"] != spec["version"] or payloads["VERSION.txt"].decode("utf-8-sig").strip() != spec["version"]:
        raise ValueError("package version mismatch")
    expected_assets = {asset["path"] for asset in manifest["assets"]}
    included_assets = {path for path, role in roles.items() if role == "runtime_asset"}
    if included_assets != expected_assets:
        raise ValueError("package asset roster mismatch")
    for asset in manifest["assets"]:
        if digest(payloads[asset["path"]]) != asset["sha256"]:
            raise ValueError("runtime asset integrity mismatch: " + asset["path"])
    # Check local import and audited literal-source dependencies against Git's tree.
    # This discovers dependencies only; it never adds files to the allowlist.
    for path, role in roles.items():
        if role != "runtime_module":
            continue
        syntax = ast.parse(payloads[path].decode("utf-8-sig"), filename=path)
        dependencies = set()
        for node in ast.walk(syntax):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for name in names:
                candidate = name.split(".")[0] + ".py"
                if candidate in objects:
                    dependencies.add(candidate)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".py") and node.value in objects:
                dependencies.add(node.value)
        missing = dependencies - payloads.keys()
        if missing:
            raise ValueError(f"package dependency missing for {path}: {sorted(missing)}")
    result = {"schema": "zhuyin-candidate-package/1", "status": "CANDIDATE_NOT_RELEASED",
              "version": spec["version"], "source_commit": commit, "source_tree": tree,
              "allowlist_sha256": digest(blob(ALLOWLIST)),
              "validation_policy": "validation-flow/no-real-tk/1",
              "window_validation": "cancelled; no GUI PASS is claimed",
              "files": [{"path": path, "role": roles[path], "bytes": len(raw), "sha256": digest(raw)}
                        for path, raw in sorted(payloads.items())]}
    return payloads, result


def build(repo, commit, output):
    payloads, manifest = prepare(repo, commit)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("refuse existing output: " + str(output))
    # Generated artifacts are outside the reviewed tree, never fed back into it.
    if output == Path(repo).resolve() or Path(repo).resolve() in output.parents:
        raise ValueError("output must be outside the source checkout")
    output.mkdir(parents=True)
    raw_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    archive = output / f"zhuyin-proofreader-{manifest['version']}-candidate-{commit[:12]}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path, raw in sorted({**payloads, "package-manifest.json": raw_manifest}.items()):
            info = zipfile.ZipInfo(path, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, raw)
    (output / "package-manifest.json").write_bytes(raw_manifest)
    (output / (archive.name + ".sha256")).write_text(digest(archive.read_bytes()) + "  " + archive.name + "\n", encoding="ascii")
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(build(args.repo, args.commit, args.output))


if __name__ == "__main__":
    main()
