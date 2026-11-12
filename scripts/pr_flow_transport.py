"""Bounded Actions transport for coordinator-attested review original bytes.

The policy is read only from the current PR's exact base Git object.  Its
GitHub-authenticated coordinator attests to captured provenance; this adapter
does not establish an agent's identity from report JSON.  No source policy or
producer is installed by this module, and missing policy means unconfigured.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import zipfile

from scripts import pr_flow_policy as policy

REPOSITORY = "discoveryray/zhuyin-proofreader"
SOURCE_PATH = ".github/pr-flow-review-source.json"
SOURCE_SCHEMA = "pr-flow-review-source/1"
ARTIFACT_SCHEMA = "pr-flow-review-artifact/1"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_MEMBERS = 512


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _positive(value, where):
    policy.require(type(value) is int and value > 0, where + ": positive integer required")


def _json(raw, *, encoding="utf-8"):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            policy.require(key not in value, "duplicate JSON key")
            value[key] = item
        return value
    return json.loads(raw.decode(encoding), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _event(event):
    pr = event["pull_request"]
    _positive(pr["number"], "PR number")
    base, head = pr["base"], pr["head"]
    policy.require(base["repo"]["full_name"] == REPOSITORY, "wrong source event repository")
    policy.require(base["ref"] in {"develop", "main"}, "unsupported source base branch")
    policy.sha(base["sha"], "event base")
    policy.sha(head["sha"], "event head")
    return pr


def _source_bytes(event, root):
    pr = _event(event)
    # SHA validation precedes Git argument construction; no working-tree config
    # or PR-controlled URL, workflow path, branch or environment override exists.
    result = subprocess.run(["git", "ls-tree", "--name-only", pr["base"]["sha"], "--", SOURCE_PATH],
                            cwd=root, capture_output=True, check=True)
    if not result.stdout.strip():
        return None
    raw = subprocess.check_output(["git", "show", pr["base"]["sha"] + ":" + SOURCE_PATH],
                                  cwd=root, stderr=subprocess.PIPE)
    policy.require(len(raw) <= MAX_JSON_BYTES, "source policy is too large")
    return raw


def _source_policy(raw, event):
    value = _json(raw)
    policy.fields(value, "schema repository repository_id workflow_id workflow_path base_ref allowed_actor_ids artifact_prefix authority",
                  "review source policy")
    policy.require(value["schema"] == SOURCE_SCHEMA and value["repository"] == REPOSITORY
                   and value["authority"] == "review-capture-coordinator", "unknown review source authority")
    for field in ("repository_id", "workflow_id"):
        _positive(value[field], field)
    _safe_path(value["workflow_path"])
    policy.require(value["workflow_path"].startswith(".github/workflows/")
                   and PurePosixPath(value["workflow_path"]).suffix in {".yml", ".yaml"}, "invalid source workflow path")
    policy.require(value["base_ref"] == _event(event)["base"]["ref"], "source policy base branch differs")
    ids = value["allowed_actor_ids"]
    policy.require(type(ids) is list and bool(ids), "source actor allowlist is empty")
    for actor in ids:
        _positive(actor, "allowed actor ID")
    policy.require(len(ids) == len(set(ids)), "duplicate allowed actor ID")
    policy.require(type(value["artifact_prefix"]) is str and
                   re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value["artifact_prefix"]) is not None,
                   "invalid artifact prefix")
    if "id" in _event(event)["base"]["repo"]:
        policy.require(type(_event(event)["base"]["repo"]["id"]) is int and
                       _event(event)["base"]["repo"]["id"] == value["repository_id"], "event repository ID differs")
    return value


def load_source_policy(event, root):
    """Return the base-committed closed policy, or None for a missing policy."""
    raw = _source_bytes(event, root)
    return None if raw is None else _source_policy(raw, event)


def _safe_path(name):
    policy.text(name, "archive member path")
    path = PurePosixPath(name)
    policy.require(bool(path.parts) and "\\" not in name and ":" not in name and not path.is_absolute()
                   and path.as_posix() == name and all(part not in {".", ".."} for part in path.parts),
                   "unsafe archive member path")
    for part in path.parts:
        policy.require(not any(ord(c) < 32 or c in '<>"|?*' for c in part) and part[-1:] not in {".", " "}
                       and re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I) is None,
                       "platform-aliased archive member path")
    return name


def _retain(path, raw):
    path = Path(path)
    for item in (path, *path.parents):
        policy.require(not item.is_symlink() and not (hasattr(item, "is_junction") and item.is_junction()),
                       "retained evidence path contains a link")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        policy.require(path.is_file() and path.read_bytes() == raw, "retained evidence bytes changed: " + path.name)
    else:
        with path.open("xb") as stream:
            stream.write(raw)
    return path


def _snapshot(output, name, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    policy.require(len(raw) <= MAX_EXPANDED_BYTES, "API metadata snapshot exceeds size limit")
    return _retain(output / name, raw)


def _reference(output, path):
    return {"path": path.relative_to(output).as_posix(), "sha256": digest(path.read_bytes())}


def _run(run, source, base):
    for field in ("id", "run_number", "run_attempt", "workflow_id"):
        _positive(run[field], "source run " + field)
    policy.require(run["event"] == "workflow_dispatch" and run["head_sha"] == base
                   and run["head_branch"] == source["base_ref"] and run["path"] == source["workflow_path"]
                   and run["workflow_id"] == source["workflow_id"], "source run workflow/base identity differs")
    policy.require(run["repository"]["full_name"] == REPOSITORY and type(run["repository"]["id"]) is int
                   and run["repository"]["id"] == source["repository_id"], "source run repository differs")
    policy.require(run["status"] == "completed" and run["conclusion"] == "success", "latest source run is unresolved or failed")
    for field in ("actor", "triggering_actor"):
        actor = run[field]
        _positive(actor["id"], field + " ID")
        policy.require(actor["id"] in source["allowed_actor_ids"], "source actor ID is not authorized")
        policy.require(type(actor["login"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor["login"]) is not None,
                       "invalid source actor login")


def _latest(rows):
    policy.require(type(rows) is list and bool(rows), "source workflow has no run")
    for row in rows:
        for field in ("id", "run_number", "run_attempt"):
            _positive(row[field], "source run inventory " + field)
    policy.require(len({r["id"] for r in rows}) == len(rows), "duplicate source run inventory ID")
    return max(rows, key=lambda row: (row["run_number"], row["id"]))


def _archive(raw):
    policy.require(type(raw) is bytes and len(raw) <= MAX_ARCHIVE_BYTES, "artifact archive size/type is invalid")
    result, aliases, total = {}, set(), 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        policy.require(0 < len(members) <= MAX_MEMBERS, "artifact member count exceeds limit")
        for member in members:
            name = _safe_path(member.filename)
            policy.require(member.orig_filename == name and not member.is_dir(), "unsupported archive directory or NUL path")
            alias = name.casefold()
            policy.require(alias not in aliases, "duplicate/case-aliased archive member")
            aliases.add(alias)
            kind = stat.S_IFMT(member.external_attr >> 16)
            policy.require(kind in {0, stat.S_IFREG} and not member.flag_bits & 1
                           and member.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "unsupported archive link/member encoding")
            policy.require(0 <= member.file_size <= MAX_MEMBER_BYTES, "artifact member exceeds size limit")
            total += member.file_size
            policy.require(total <= MAX_EXPANDED_BYTES, "expanded artifact exceeds size limit")
            data = archive.read(member)
            policy.require(len(data) == member.file_size, "artifact expanded size differs")
            result[name] = data
    for name in result:
        policy.require(not any(parent.as_posix().casefold() in aliases for parent in PurePosixPath(name).parents
                               if parent.as_posix() != "."), "archive file/directory alias")
    return result


def _tuple(ref, source_file, sha256):
    policy.text(ref, "attachment report_ref")
    policy.text(source_file, "attachment source_file")
    policy.require(Path(source_file).is_absolute() or PureWindowsPath(source_file).is_absolute(),
                   "attachment original source_file must be absolute")
    policy.sha(sha256, "attachment sha256", 64)
    return source_file, sha256, ref


def _unpack(raw, output, expected, event):
    members = _archive(raw)
    policy.require("manifest.json" in members and "capture.json" in members, "artifact manifest/capture is missing")
    policy.require(len(members["manifest.json"]) <= MAX_JSON_BYTES and len(members["capture.json"]) <= MAX_JSON_BYTES,
                   "manifest/capture exceeds size limit")
    manifest, capture = _json(members["manifest.json"]), _json(members["capture.json"])
    policy.fields(manifest, "schema candidate capture_sha256 attachments", "review artifact manifest")
    policy.require(manifest["schema"] == ARTIFACT_SCHEMA, "unknown review artifact schema")
    policy.validate_candidate(manifest["candidate"])
    candidate, pr = manifest["candidate"], _event(event)
    policy.require((candidate["repository"], candidate["base"], candidate["head"]) ==
                   (REPOSITORY, pr["base"]["sha"], pr["head"]["sha"]), "artifact candidate differs from current event")
    policy.require(expected is None or candidate == expected, "artifact candidate differs from expected task/candidate")
    policy.fields(capture, "schema policy coordinator candidate classification implementers review_originals", "review capture")
    policy.require(capture["schema"] == "pr-flow-ci-capture/1" and capture["policy"] == policy.POLICY_ID
                   and capture["candidate"] == candidate, "capture schema/candidate differs")
    policy.sha(manifest["capture_sha256"], "capture SHA256", 64)
    policy.require(digest(members["capture.json"]) == manifest["capture_sha256"], "capture original hash mismatch")
    attachments = manifest["attachments"]
    policy.require(type(attachments) is list, "attachments must be a list")
    mapping, paths = {}, set()
    for item in attachments:
        policy.fields(item, "report_ref source_file sha256 path", "review attachment")
        key = _tuple(item["report_ref"], item["source_file"], item["sha256"])
        name = _safe_path(item["path"])
        policy.require(name not in {"manifest.json", "capture.json"} and name not in paths and key not in mapping,
                       "duplicate or reserved attachment binding")
        policy.require(name in members and digest(members[name]) == item["sha256"], "attachment bytes/hash mismatch")
        mapping[key], paths = name, paths | {name}
    policy.require(set(members) == paths | {"manifest.json", "capture.json"}, "unbound artifact member")
    needed = set()
    def original(pointer):
        policy.fields(pointer, "report_ref source_file sha256 backups", "transport original")
        key = _tuple(pointer["report_ref"], pointer["source_file"], pointer["sha256"])
        policy.require(key in mapping, "original attachment binding is missing")
        needed.add(key)
        policy.require(type(pointer["backups"]) is list, "original backups must be a list")
        for backup in pointer["backups"]:
            policy.fields(backup, "source_file sha256", "transport backup")
            backup_key = _tuple(pointer["report_ref"], backup["source_file"], backup["sha256"])
            policy.require(backup_key in mapping, "backup attachment binding is missing")
            needed.add(backup_key)
        return members[mapping[key]]
    policy.require(type(capture["review_originals"]) is list, "review originals must be a list")
    for pointer in capture["review_originals"]:
        report = _json(original(pointer), encoding="utf-8-sig")
        policy.require(type(report["review_checks"]) is list and type(report["resolutions"]) is list,
                       "report checks/resolutions must be lists")
        for check in report["review_checks"]:
            original(check)
        for resolution in report["resolutions"]:
            original(resolution["evidence"])
    policy.require(needed == set(mapping), "attachment is not bound to a canonical pointer")
    extracted = output / "originals"
    for name, data in members.items():
        _retain(extracted / name, data)
    def resolver(source_file, sha256, report_ref):
        key = _tuple(report_ref, source_file, sha256)
        policy.require(key in mapping, "unknown original resolver binding")
        path = (extracted / mapping[key]).absolute()
        for item in (path, *path.parents):
            policy.require(not item.is_symlink() and not (hasattr(item, "is_junction") and item.is_junction()),
                           "transported original path contains a link")
        policy.require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == sha256,
                       "transported original bytes changed")
        return path
    # Shared policy checks every canonical report, nested check/resolution and
    # every same-bytes backup without rewriting any original pointer or bytes.
    from scripts.pr_review_gate import validate_tiered_reviews
    validate_tiered_reviews(candidate, capture["classification"], capture["implementers"],
                            capture["review_originals"], original_resolver=resolver)
    return capture, resolver, extracted / "capture.json"


def fetch_capture(client, candidate, event, output, root):
    """Fetch through fixed repository endpoints; return capture/resolver/source.

    candidate may be None before reading a manifest. Its repository/base/head
    still bind to the current PR; downstream bind_event checks exact Git scope.
    API snapshots retain checked parsed API objects, not raw HTTP wire bytes.
    """
    raw_policy = _source_bytes(event, root)
    policy.require(raw_policy is not None, "review transport source is unconfigured")
    source = _source_policy(raw_policy, event)
    pr = _event(event)
    if candidate is not None:
        policy.validate_candidate(candidate)
        policy.require((candidate["repository"], candidate["base"], candidate["head"]) ==
                       (REPOSITORY, pr["base"]["sha"], pr["head"]["sha"]), "expected candidate differs from event")
    output = Path(output).absolute()
    retained = {"policy": _reference(output, _retain(output / "source-policy.json", raw_policy))}
    def snapshot(name, value):
        retained[name] = _reference(output, _snapshot(output, name + ".json", value))
        return value
    repository = snapshot("repository", client.request(""))
    policy.require(repository["full_name"] == REPOSITORY and type(repository["id"]) is int
                   and repository["id"] == source["repository_id"], "GitHub repository identity differs")
    current_pr = snapshot("pr", client.request(f"pulls/{pr['number']}"))
    merged_source = pr.get("merged") is True
    policy.require(type(current_pr["number"]) is int and current_pr["number"] == pr["number"]
                   and ((current_pr["state"] == "open" and not merged_source)
                        or (current_pr["state"] == "closed" and current_pr.get("merged") is True and merged_source)),
                   "source PR is no longer the current open PR or actual merged source PR")
    if merged_source:
        policy.sha(pr["merge_commit_sha"], "source actual merge SHA")
        policy.require(current_pr["merge_commit_sha"] == pr["merge_commit_sha"], "source actual merge SHA differs")
    for side in ("base", "head"):
        actual, expected = current_pr[side], pr[side]
        policy.require((actual["sha"], actual["ref"], actual["repo"]["full_name"]) ==
                       (expected["sha"], expected["ref"], expected["repo"]["full_name"]),
                       "GitHub current PR differs from event " + side)
    policy.require(type(current_pr["base"]["repo"]["id"]) is int and
                   current_pr["base"]["repo"]["id"] == source["repository_id"], "GitHub current PR repository ID differs")
    endpoint = f"actions/workflows/{source['workflow_id']}/runs?event=workflow_dispatch&head_sha={pr['base']['sha']}"
    rows = snapshot("runs-before", client.pages(endpoint, "workflow_runs"))
    latest = _latest(rows)
    _run(latest, source, pr["base"]["sha"])
    run = snapshot("run-before", client.request(f"actions/runs/{latest['id']}"))
    _run(run, source, pr["base"]["sha"])
    policy.require((run["id"], run["run_attempt"]) == (latest["id"], latest["run_attempt"]), "source run attempt is stale")
    for field in ("actor", "triggering_actor"):
        actor = run[field]
        permission = snapshot("permission-" + field, client.request("collaborators/" + actor["login"] + "/permission"))
        user = permission["user"]
        # GitHub's permission is a legacy base role: maintain maps to write.
        # role_name is required as well; plain write/custom roles never qualify.
        policy.text(permission["permission"], "collaborator permission")
        policy.text(permission["role_name"], "collaborator role_name")
        policy.require(type(user["id"]) is int and user["id"] == actor["id"] and user["login"] == actor["login"]
                       and (permission["permission"], permission["role_name"]) in {("admin", "admin"), ("write", "maintain")},
                       "source actor collaborator authority differs")
    artifacts = snapshot("artifacts", client.pages(f"actions/runs/{run['id']}/artifacts", "artifacts"))
    policy.require(type(artifacts) is list, "source artifacts must be a list")
    wanted = f"{source['artifact_prefix']}-{pr['number']}-{pr['head']['sha']}-{run['id']}-{run['run_attempt']}"
    matches = [a for a in artifacts if a.get("name") == wanted]
    policy.require(len(matches) == 1, "source needs exactly one candidate-bound artifact")
    artifact = matches[0]
    _positive(artifact["id"], "artifact ID")
    policy.require(artifact["expired"] is False and type(artifact["size_in_bytes"]) is int
                   and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES, "source artifact is expired or oversized")
    identity = artifact["workflow_run"]
    policy.require(type(identity["id"]) is int and identity["id"] == run["id"]
                   and type(identity["repository_id"]) is int and identity["repository_id"] == source["repository_id"]
                   and identity["head_sha"] == pr["base"]["sha"] and identity["head_branch"] == source["base_ref"],
                   "artifact workflow/repository identity differs")
    raw = client.request(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    policy.require(type(raw) is bytes and len(raw) <= MAX_ARCHIVE_BYTES, "artifact download exceeds limit")
    archive = _retain(output / "artifact.zip", raw)
    retained["archive"] = _reference(output, archive)
    policy.require(artifact["digest"] == "sha256:" + digest(raw) and len(raw) == artifact["size_in_bytes"],
                   "GitHub artifact digest/size differs")
    capture, resolver, capture_path = _unpack(raw, output, candidate, event)
    # A rerun/new run between inventory and download must not validate an old
    # attempt. These calls also retain the checked end-of-download API state.
    after = snapshot("runs-after", client.pages(endpoint, "workflow_runs"))
    current = snapshot("run-after", client.request(f"actions/runs/{run['id']}"))
    _run(_latest(after), source, pr["base"]["sha"])
    _run(current, source, pr["base"]["sha"])
    policy.require(_latest(after) == latest and current == run, "source run/attempt changed during retrieval")
    retained["capture"] = _reference(output, capture_path)
    metadata = {"schema": "pr-flow-review-source-evidence/1", "authority": source["authority"],
                "repository": REPOSITORY, "repository_id": source["repository_id"],
                "policy_git": {"base": pr["base"]["sha"], "path": SOURCE_PATH, "sha256": digest(raw_policy)},
                "run_id": run["id"], "run_attempt": run["run_attempt"], "artifact_id": artifact["id"],
                "artifact_name": wanted, "artifacts": retained}
    _snapshot(output, "source.json", metadata)
    return {"capture": capture, "original_resolver": resolver, "source": metadata}


def verify_retained_capture(metadata, event, output, root, *, candidate=None):
    """Verify retained context/bytes without claiming fresh API authentication.

    These parsed API snapshots preserve the checked retrieval context. Their
    hashes cannot prove that an API call occurred; the coordinator must verify
    live provenance separately. A retained open-PR snapshot stays historical
    when a caller separately verifies the current actual merged source PR.
    """
    policy.fields(metadata, "schema authority repository repository_id policy_git run_id run_attempt artifact_id artifact_name artifacts",
                  "retained review source metadata")
    policy.require(metadata["schema"] == "pr-flow-review-source-evidence/1"
                   and metadata["authority"] == "review-capture-coordinator"
                   and metadata["repository"] == REPOSITORY, "unknown retained source authority")
    for field in ("repository_id", "run_id", "run_attempt", "artifact_id"):
        _positive(metadata[field], "retained " + field)
    policy.fields(metadata["policy_git"], "base path sha256", "retained source Git policy")
    policy.require(metadata["policy_git"]["base"] == _event(event)["base"]["sha"]
                   and metadata["policy_git"]["path"] == SOURCE_PATH, "retained source Git base/path differs")
    policy.sha(metadata["policy_git"]["sha256"], "retained policy SHA256", 64)
    names = {"policy": "source-policy.json", "repository": "repository.json", "pr": "pr.json",
             "runs-before": "runs-before.json", "runs-after": "runs-after.json",
             "run-before": "run-before.json", "run-after": "run-after.json",
             "permission-actor": "permission-actor.json", "permission-triggering_actor": "permission-triggering_actor.json",
             "artifacts": "artifacts.json", "archive": "artifact.zip", "capture": "originals/capture.json"}
    refs = metadata["artifacts"]
    policy.require(type(refs) is dict and set(refs) == set(names), "missing/unknown retained source references")
    output = Path(output).absolute()
    saved = {}
    for key, name in names.items():
        pointer = refs[key]
        policy.fields(pointer, "path sha256", "retained source reference")
        policy.sha(pointer["sha256"], "retained reference SHA256", 64)
        policy.require(pointer["path"] == name, "retained source reference path differs")
        _safe_path(pointer["path"])
        path = output / pointer["path"]
        for part in (path, *path.parents):
            policy.require(not part.is_symlink() and not (hasattr(part, "is_junction") and part.is_junction()),
                           "retained source reference contains a link")
        limit = MAX_ARCHIVE_BYTES if key == "archive" else MAX_EXPANDED_BYTES
        policy.require(path.is_file() and path.stat().st_size <= limit, "retained source file missing or oversized")
        raw = path.read_bytes()
        policy.require(digest(raw) == pointer["sha256"], "retained source reference hash differs")
        saved[key] = raw if key in {"policy", "archive", "capture"} else _json(raw)
    base_policy = _source_bytes(event, root)
    policy.require(base_policy is not None and base_policy == saved["policy"]
                   and digest(base_policy) == metadata["policy_git"]["sha256"], "retained policy differs from authoritative Git base")
    run = saved["run-before"]
    class RetainedAPI:
        def __init__(self):
            self.runs = self.inventories = self.permissions = 0

        def request(self, endpoint, *, binary=False):
            if endpoint == "": return saved["repository"]
            if endpoint == f"pulls/{_event(event)['number']}": return saved["pr"]
            if endpoint == f"actions/runs/{metadata['run_id']}":
                self.runs += 1
                policy.require(self.runs <= 2, "unexpected retained run read")
                return saved["run-before" if self.runs == 1 else "run-after"]
            if endpoint == f"actions/artifacts/{metadata['artifact_id']}/zip" and binary:
                return saved["archive"]
            fields = ("actor", "triggering_actor")
            if self.permissions < 2:
                field = fields[self.permissions]
                if endpoint == "collaborators/" + run[field]["login"] + "/permission":
                    self.permissions += 1
                    return saved["permission-" + field]
            raise policy.PolicyError("unexpected retained source API endpoint")

        def pages(self, endpoint, key=None):
            source = _source_policy(saved["policy"], event)
            expected = f"actions/workflows/{source['workflow_id']}/runs?event=workflow_dispatch&head_sha={_event(event)['base']['sha']}"
            if endpoint == expected and key == "workflow_runs":
                self.inventories += 1
                policy.require(self.inventories <= 2, "unexpected retained inventory read")
                return saved["runs-before" if self.inventories == 1 else "runs-after"]
            if endpoint == f"actions/runs/{metadata['run_id']}/artifacts" and key == "artifacts":
                return saved["artifacts"]
            raise policy.PolicyError("unexpected retained source paged endpoint")
    historical_pr = dict(_event(event))
    # Preserve immutable PR context, never relabel an old open snapshot as a
    # fresh assertion about the current closed/merged state.
    historical_pr["merged"] = saved["pr"].get("merged") is True
    if historical_pr["merged"]:
        historical_pr["merge_commit_sha"] = saved["pr"]["merge_commit_sha"]
    result = fetch_capture(RetainedAPI(), candidate, {"pull_request": historical_pr}, output, root)
    policy.require(result["source"] == metadata, "retained source metadata differs from checked context")
    result["verification_context"] = "retained-source-integrity-only"
    return result
