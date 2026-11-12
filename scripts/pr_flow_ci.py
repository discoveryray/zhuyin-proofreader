"""Inactive tiered CI wiring and bounded, file-backed documentation checks.

CAPTURE names the legacy local handoff; that file supplies no source authority.
Configured Actions transport instead binds coordinator-attested originals to
base-owned policy and checked GitHub API identity. Missing trusted sources keep
full CI. Neutral PR checks assert no classification or reviewer identity, and
no CLI argument or environment variable can enable the policy.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
from html import unescape
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import subprocess
import sys
import zipfile
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import pr_flow_policy as policy

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = Path("tmp/pr-review-automation/pr-flow-ci/current-capture.json")
SCHEMA = "pr-flow-documentation-checks/1"
PR_SCHEMA = "pr-flow-documentation-checks/2"
DOCUMENT_SUFFIXES = {".md", ".rst", ".txt"}
CHECKS = ("bytes", "local_links", "diff")
FULL_STEPS = (
    "Upgrade pip", "Install development dependencies", "Capture main full environment",
    "Validate runtime asset integrity", "Audit test entrypoint coverage", "Run full pytest suite",
    "Verify active test execution", "Compile Python sources", "Check committed whitespace (pull request)",
    "Check committed whitespace (push)", "Check repository working tree", "Produce current validation coverage",
    "Verify current validation coverage", "Upload main full evidence",
)
DOC_STEPS = ("Run required documentation checks", "Verify required documentation checks", "Upload documentation evidence")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        policy.require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def bind_git(candidate, root=ROOT):
    """Verify the complete cumulative file list and actual Git objects."""
    policy.validate_candidate(candidate)
    policy.require(git(root, "rev-parse", candidate["head"] + "^{tree}").decode().strip() == candidate["tree"],
                   "candidate Git tree differs")
    policy.require(git(root, "merge-base", candidate["base"], candidate["head"]).decode().strip() == candidate["merge_base"],
                   "candidate merge base differs")
    subprocess.run(["git", "merge-base", "--is-ancestor", candidate["baseline"], candidate["head"]],
                   cwd=root, check=True, capture_output=True)
    actual = git(root, "diff", "--name-only", "-z", candidate["baseline"], candidate["head"]).decode("utf-8").rstrip("\0").split("\0")
    policy.require(sorted(actual) == sorted(candidate["changed_files"]), "complete cumulative changed files differ")


def bind_event(candidate, event, environment, root=ROOT):
    bind_git(candidate, root)
    policy.require(environment["GITHUB_EVENT_NAME"] == "pull_request", "documentation route requires a current PR event")
    pr = event["pull_request"]
    policy.require((environment["GITHUB_REPOSITORY"], pr["base"]["repo"]["full_name"], pr["base"]["sha"], pr["head"]["sha"])
                   == (candidate["repository"], candidate["repository"], candidate["base"], candidate["head"]),
                   "current event repository/base/head differs")
    policy.require(pr["base"]["ref"] in {"develop", "main"}, "unsupported PR target")
    checkout_sha = git(root, "rev-parse", "HEAD").decode().strip()
    parents = git(root, "show", "-s", "--format=%P", "HEAD").decode().strip().split()
    policy.require(environment["GITHUB_SHA"] == checkout_sha and parents == [candidate["base"], candidate["head"]]
                   and checkout_sha not in parents, "current synthetic merge SHA/ordered parents differ")
    ci = {"run_id": int(environment["GITHUB_RUN_ID"]), "attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
          "job": environment["GITHUB_JOB"], "event": "pull_request"}
    policy.require(ci["run_id"] > 0 and ci["attempt"] > 0 and ci["job"] == "test", "invalid current CI identity")
    checkout = {"number": pr["number"], "repository": candidate["repository"],
                "base_repository": pr["base"]["repo"]["full_name"], "head_repository": pr["head"]["repo"]["full_name"],
                "base_ref": pr["base"]["ref"], "head_ref": pr["head"]["ref"],
                "base_sha": candidate["base"], "head_sha": candidate["head"],
                "checkout_sha": checkout_sha, "checkout_tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
                "checkout_parents": parents}
    policy.require(type(checkout["number"]) is int and checkout["number"] > 0, "invalid current PR number")
    return {"ci": ci, "checkout": checkout}


def validated_documentation_plan(capture, event, environment, root=ROOT, *, original_resolver=None):
    """Pure validation for the future handoff; never enables public dispatch."""
    policy.fields(capture, "schema policy coordinator candidate classification implementers review_originals", "review capture")
    policy.require(capture["schema"] == "pr-flow-ci-capture/1" and capture["policy"] == policy.POLICY_ID,
                   "unknown review capture")
    coordinator = capture["coordinator"]
    policy.fields(coordinator, "actor source_refs authorization_ref independent_sources_checked", "coordinator capture")
    policy.text(coordinator["actor"], "coordinator.actor")
    policy.texts(coordinator["source_refs"], "coordinator.source_refs", nonempty=True)
    policy.text(coordinator["authorization_ref"], "coordinator.authorization_ref")
    policy.require(coordinator["independent_sources_checked"] is True,
                   "coordinator must independently check original provenance; author JSON is insufficient")
    candidate = capture["candidate"]
    binding = bind_event(candidate, event, environment, root)
    # Canonical originals, author exclusion, classification and latest review
    # chains share the gate's validator. Hashes cannot prove an actor's identity.
    from scripts.pr_review_gate import validate_tiered_reviews
    reviewed = validate_tiered_reviews(candidate, capture["classification"], capture["implementers"], capture["review_originals"],
                                      original_resolver=original_resolver)
    ready = reviewed["code_ready"] and reviewed["classification"]["confirmed"] and reviewed["classification"]["tier"] == "docs"
    # The initial bounded checker supports existing Markdown files. Other
    # document formats/deletions retain full CI until a reviewed checker exists.
    supported = all(PurePosixPath(name).suffix.lower() == ".md" for name in candidate["changed_files"])
    if ready and supported:
        for name in candidate["changed_files"]:
            git(root, "cat-file", "blob", binding["checkout"]["checkout_sha"] + ":" + name)
    return {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID,
            "route": "docs" if ready and supported else "full", "candidate": candidate,
            "checks_route": "docs" if ready and supported else "docs_fallback_full" if ready else "full",
            "classification_tier": reviewed["classification"]["tier"],
            "reason": "confirmed-documentation" if ready and supported else "full-validation-required", **binding}


def dispatch(event, environment, root=ROOT):
    name, ref = environment.get("GITHUB_EVENT_NAME"), environment.get("GITHUB_REF")
    if name == "push" and ref == "refs/heads/develop":
        route = "short"
    elif (name == "pull_request" and event["pull_request"]["base"]["ref"] in {"develop", "main"}) or (name == "push" and ref == "refs/heads/main"):
        route = "full"
    else:
        raise policy.PolicyError("unsupported current validation event")
    result = {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID, "route": route,
              "reason": "policy-inactive" if not policy.ENABLED else "trusted-capture-unavailable"}
    if policy.ENABLED:
        result["checks_route"] = route
    if policy.ENABLED and name == "pull_request":
        scope, binding = pr_execution_scope(event, environment, root)
        result.update(execution_scope=scope, **binding)
    if not policy.ENABLED or route != "full" or name != "pull_request":
        return result
    try:
        from scripts.pr_flow_transport import fetch_capture, load_source_policy
        if load_source_policy(event, root) is None:
            result["reason"] = "trusted-source-unconfigured"
            return result
        from scripts.validation_evidence import GitHub
        transfer = fetch_capture(GitHub(environment.get("GITHUB_TOKEN")), None, event,
                                 Path(root) / "tmp/pr-flow-ci/transport", root)
        result = validated_documentation_plan(transfer["capture"], event, environment, root,
                                             original_resolver=transfer["original_resolver"])
        result["execution_scope"] = scope
        result["source"] = transfer["source"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        result.update(route="full", reason="invalid-trusted-capture: " + str(error))
    return result


def _tested_checkout(root, candidate, tested_sha=None):
    """Resolve the actual integration object, never substitute the feature tree."""
    bind_git(candidate, root)
    sha = tested_sha or git(root, "rev-parse", "HEAD").decode().strip()
    policy.sha(sha, "tested checkout SHA")
    identity = {"head": sha, "tree": git(root, "rev-parse", sha + "^{tree}").decode().strip(),
                "parents": git(root, "show", "-s", "--format=%P", sha).decode().strip().split()}
    policy.require(identity["parents"] == [candidate["base"], candidate["head"]] and sha not in identity["parents"],
                   "tested checkout must be the actual ordered PR integration")
    return identity


def _bound_checkout(root, candidate, checkout, *, current=False):
    identity = _tested_checkout(root, candidate, checkout["checkout_sha"])
    policy.require(identity == {"head": checkout["checkout_sha"], "tree": checkout["checkout_tree"],
                                "parents": checkout["checkout_parents"]}, "tested checkout Git identity differs from binding")
    policy.require((checkout["base_sha"], checkout["head_sha"]) == (candidate["base"], candidate["head"]),
                   "tested checkout candidate binding differs")
    if current:
        policy.require(git(root, "rev-parse", "HEAD").decode().strip() == identity["head"],
                       "tested checkout changed during documentation checks")
    return identity


def _markdown(root, candidate, tested_sha):
    result = {}
    for name in candidate["changed_files"]:
        policy.require(PurePosixPath(name).suffix.lower() == ".md", "bounded documentation checker requires Markdown")
        raw = git(root, "cat-file", "blob", tested_sha + ":" + name)
        text = raw.decode("utf-8")
        policy.require(bool(text.strip()) and "\0" not in text, "empty or binary documentation")
        result[name] = (raw, text)
    return result


def pr_execution_scope(event, environment, root=ROOT):
    """Derive a neutral PR scope from the runner event and actual Git objects."""
    policy.require(environment.get("GITHUB_EVENT_NAME") == "pull_request", "supplemental documentation requires PR event")
    pr = event["pull_request"]
    repository = environment["GITHUB_REPOSITORY"]
    policy.require(repository == pr["base"]["repo"]["full_name"] == "discoveryray/zhuyin-proofreader"
                   and pr["base"]["ref"] in {"develop", "main"}, "unsupported supplemental PR repository/target")
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    policy.sha(base, "PR base"); policy.sha(head, "PR head")
    merge_base = git(root, "merge-base", base, head).decode().strip()
    files = git(root, "diff", "--name-only", "-z", base + "..." + head).decode("utf-8").rstrip("\0")
    scope = {"repository": repository, "base": base, "head": head, "merge_base": merge_base,
             "changed_files": files.split("\0") if files else []}
    identity = _pr_scope_checkout(scope, environment["GITHUB_SHA"], root)
    policy.require(git(root, "rev-parse", "HEAD").decode().strip() == identity["head"], "runner PR checkout differs")
    ci = {"run_id": int(environment["GITHUB_RUN_ID"]), "attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
          "job": environment["GITHUB_JOB"], "event": "pull_request"}
    policy.require(ci["run_id"] > 0 and ci["attempt"] > 0 and ci["job"] == "test", "invalid supplemental CI identity")
    checkout = {"number": pr["number"], "repository": repository,
                "base_repository": pr["base"]["repo"]["full_name"], "head_repository": pr["head"]["repo"]["full_name"],
                "base_ref": pr["base"]["ref"], "head_ref": pr["head"]["ref"], "base_sha": base, "head_sha": head,
                "checkout_sha": identity["head"], "checkout_tree": identity["tree"], "checkout_parents": identity["parents"]}
    policy.require(type(checkout["number"]) is int and checkout["number"] > 0, "invalid supplemental PR number")
    return scope, {"ci": ci, "checkout": checkout}


def _pr_scope_checkout(scope, tested_sha, root):
    policy.fields(scope, "repository base head merge_base changed_files", "PR execution scope")
    policy.require(scope["repository"] == "discoveryray/zhuyin-proofreader", "wrong supplemental repository")
    for field in ("base", "head", "merge_base"):
        policy.sha(scope[field], "execution scope." + field)
    policy.texts(scope["changed_files"], "execution scope.changed_files")
    for name in scope["changed_files"]:
        path = PurePosixPath(name)
        policy.require("\\" not in name and ":" not in name and not path.is_absolute() and path.as_posix() == name
                       and all(part not in {".", ".."} for part in path.parts), "invalid PR execution path")
    policy.require(git(root, "merge-base", scope["base"], scope["head"]).decode().strip() == scope["merge_base"], "supplemental merge base differs")
    raw = git(root, "diff", "--name-only", "-z", scope["base"] + "..." + scope["head"]).decode("utf-8").rstrip("\0")
    policy.require((raw.split("\0") if raw else []) == scope["changed_files"], "supplemental PR diff scope differs")
    policy.sha(tested_sha, "supplemental tested SHA")
    identity = {"head": tested_sha, "tree": git(root, "rev-parse", tested_sha + "^{tree}").decode().strip(),
                "parents": git(root, "show", "-s", "--format=%P", tested_sha).decode().strip().split()}
    policy.require(identity["parents"] == [scope["base"], scope["head"]] and tested_sha not in identity["parents"],
                   "supplemental tested checkout is not ordered PR integration")
    return identity


def document_paths(paths):
    return [name for name in paths if PurePosixPath(name).suffix.lower() in DOCUMENT_SUFFIXES]


def raw_commit_identity(raw):
    """Recompute an actual Git commit object without requiring an old ref."""
    policy.require(type(raw) is bytes and b"\n\n" in raw, "malformed raw tested commit")
    header = raw.split(b"\n\n", 1)[0].split(b"\n")
    trees = [line[5:].decode("ascii") for line in header if line.startswith(b"tree ")]
    parents = [line[7:].decode("ascii") for line in header if line.startswith(b"parent ")]
    policy.require(len(trees) == 1 and len(parents) == 2, "tested commit requires tree and ordered merge parents")
    for value in [*trees, *parents]:
        policy.sha(value, "raw tested commit object")
    sha = hashlib.sha1(b"commit " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    return {"head": sha, "tree": trees[0], "parents": parents}


def inspect_pr_documentation(scope, check, root=ROOT, *, tested_sha):
    tested = _pr_scope_checkout(scope, tested_sha, root)
    paths = document_paths(scope["changed_files"])
    # This adapter makes no task, classification, authorization or exemption claim.
    candidate = {"changed_files": paths, "baseline": scope["merge_base"], "head": scope["head"]}
    return _inspect_documentation(candidate, check, root, tested)


def _html_tag(text, index):
    end, quote = index + 1, None
    while end < len(text):
        value = text[end]
        if quote:
            if value == quote: quote = None
        elif value in {"'", '"'}: quote = value
        elif value == ">": break
        end += 1
    policy.require(end < len(text), "incomplete HTML documentation tag")
    return text[index:end + 1], end + 1


def _canonical_html_tag(tag):
    name = r"[A-Za-z][A-Za-z0-9-]*"
    attribute = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
    value = r'''(?:"[^"]*"|'[^']*'|[^ \t\r\n\f"'=<>`]+)'''
    assigned = r"(?:[ \t]*=[ \t]*" + value + r")?"
    opening = r"<" + name + r"(?:[ \t]+" + attribute + assigned + r")*[ \t]*/?>"
    closing = r"</" + name + r"[ \t]*>"
    return re.fullmatch("(?:" + opening + "|" + closing + ")", tag) is not None


def _documentation_lex(text):
    """Keep original block context; masking cannot manufacture HTML or ATX."""
    text = "\n".join(text.splitlines())
    visible, headings, index, fence = [], [], 0, None
    raw_html, raw_pre, context, container = False, False, "blank", False
    block_tags = set("blockquote dd details div dl dt figcaption figure h1 h2 h3 h4 h5 h6 hr li ol p pre table tbody td tfoot th thead tr ul".split())
    def mask(raw):
        return "".join("\n" if char == "\n" else " " for char in raw)
    while index < len(text):
        if index == 0 or text[index - 1] == "\n":
            line_end = text.find("\n", index)
            line_end = len(text) if line_end == -1 else line_end
            line = text[index:line_end]
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence is not None:
                if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                    fence = None; context = "block"
                visible.append(mask(text[index:line_end + 1]))
                index = line_end + 1
                continue
            container = bool(re.match(r"^(?: {4}| *\t| {0,3}(?:>|[-+*]\s|\d+[.)]\s))", line)) and not raw_html and not raw_pre
            if not line.strip() and not raw_pre:
                raw_html = False; context = "blank"
            elif not raw_html and not raw_pre and not container:
                opening = re.match(r"^ {0,3}</?([A-Za-z][A-Za-z0-9-]*)(?:\s|/?>|$)", line)
                if opening:
                    start = index + len(line) - len(line.lstrip(" "))
                    tag, end = _html_tag(text, start)
                    single = end <= line_end and not text[end:line_end].strip()
                    name = opening[1].lower()
                    if name in block_tags:
                        raw_html = True; context = "block"
                    elif name == "summary":
                        raise policy.PolicyError("unsupported summary outside a known HTML block")
                    elif single:
                        policy.require(_canonical_html_tag(tag), "unsupported noncanonical HTML documentation tag")
                        policy.require(context != "unknown", "unsupported HTML tag after container context")
                        # Type 7 is one complete tag on the line, and cannot
                        # interrupt a paragraph. Inline <a>text</a> is neither.
                        if context != "paragraph": raw_html = True; context = "block"
            if match:
                policy.require(not raw_html and not raw_pre, "unsupported raw HTML/code-fence mixture")
                policy.require(match[1][0] != "`" or "`" not in match[2], "unsupported Markdown fence info")
                fence = match[1]
                visible.append(mask(text[index:line_end + 1]))
                index = line_end + 1
                continue
            if not raw_html and not raw_pre and line.strip():
                heading = re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$", line)
                if heading and not container:
                    policy.require(context != "unknown", "unsupported heading after container context")
                    headings.append(heading[1]); context = "block"
                elif container:
                    context = "unknown"
                elif context != "unknown":
                    context = "paragraph"
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            policy.require(end != -1, "unclosed HTML documentation comment")
            visible.append(mask(text[index:end + 3])); index = end + 3
        elif text[index] == "\\" and not raw_html and not raw_pre and index + 1 < len(text) and text[index + 1] in r"!\"#$%&'()*+,-./:;<=>?@[\]^_`{|}~":
            # Escaped punctuation is text. In particular, escaped '<' must not
            # be delivered to HTMLParser as an actual opening delimiter.
            visible.append(mask(text[index:index + 2])); index += 2
        elif text[index] == "`" and not raw_html and not raw_pre:
            run = re.match(r"`+", text[index:])[0]
            end = next((index + len(run) + match.start() for match in re.finditer(r"`+", text[index + len(run):])
                        if len(match[0]) == len(run)), None)
            if end is None:
                visible.append(run); index += len(run)
            else:
                content = text[index + len(run):end]
                policy.require(re.search(r"\n\s*\n|\n[ \t]*(?:<|`{3,}|~{3,}|#{1,6}\s|>|(?:[-+*]|\d+[.)])(?:[ \t]+|(?=\n|$))|[=-]+[ \t]*(?=\n|$)|[-*_](?:[ \t]*[-*_]){2,}[ \t]*(?=\n|$))", content) is None,
                               "unsupported code span across Markdown/HTML blocks")
                visible.append(mask(text[index:end + len(run)]))
                index = end + len(run)
        elif text[index] == "<" and re.match(r"</?[A-Za-z]", text[index:]):
            policy.require(not container, "unsupported HTML in indented/container documentation")
            tag, end = _html_tag(text, index)
            policy.require(raw_html or raw_pre or _canonical_html_tag(tag), "unsupported noncanonical HTML documentation tag")
            if re.match(r"<pre(?:\s|/?>)", tag, re.I): raw_pre = True; raw_html = True
            visible.append(tag)
            if re.match(r"</pre\s*>", tag, re.I): raw_pre = False
            index = end
        else:
            visible.append(text[index])
            index += 1
    policy.require(fence is None, "unclosed Markdown code fence")
    return "".join(visible), headings


def _visible(text):
    return _documentation_lex(text)[0]


def _heading_text(title):
    """Only plain ATX titles and ordinary inline-code words have slug proof."""
    result, plain, index = [], [], 0
    def flush():
        result.append(unescape("".join(plain))); plain.clear()
    while index < len(title):
        if title[index] == "`":
            flush()
            run = re.match(r"`+", title[index:])[0]
            end = next((index + len(run) + match.start() for match in re.finditer(r"`+", title[index + len(run):]) if len(match[0]) == len(run)), None)
            policy.require(end is not None, "unsupported unmatched code delimiter in heading")
            value = title[index + len(run):end]
            policy.require(not any(char in value for char in "<>&\n\r"), "unsupported HTML-like code in heading")
            result.append(value); index = end + len(run)
        elif title[index] == "\\" and index + 1 < len(title):
            flush()
            result.append(title[index + 1]); index += 2
        else:
            policy.require(title[index] not in "[]<>_~", "unsupported rich Markdown heading")
            plain.append(title[index]); index += 1
    flush()
    return "".join(result)


class _DocumentationHTML(HTMLParser):
    """Finite display HTML only; unsupported resource syntax never passes."""
    TAGS = set("a img details summary div span p br hr h1 h2 h3 h4 h5 h6 table thead tbody tfoot tr th td ul ol li dl dt dd strong em b i s del ins sub sup kbd samp code pre blockquote figure figcaption".split())
    GLOBAL_ATTRIBUTES = {"id", "class", "title", "align", "role", "aria-label", "dir", "lang"}
    ATTRIBUTES = {"a": {"href", "name", "target", "rel"}, "img": {"src", "alt", "width", "height", "loading", "decoding"},
                  "details": {"open"}, "th": {"colspan", "rowspan", "scope"}, "td": {"colspan", "rowspan"},
                  "ol": {"start", "reversed", "type"}, "li": {"value"}}

    def __init__(self):
        # Attribute values are decoded by HTMLParser independently; keeping
        # text references separate prevents escaped examples becoming tags.
        super().__init__(convert_charrefs=False)
        self.targets, self.anchors = [], set()

    def handle_starttag(self, tag, attrs):
        policy.require(tag in self.TAGS, "unsupported HTML documentation tag: " + tag)
        seen = set()
        for name, value in attrs:
            policy.require(name not in seen, "duplicate HTML documentation attribute: " + name)
            seen.add(name)
            policy.require(name in self.GLOBAL_ATTRIBUTES | self.ATTRIBUTES.get(tag, set()), "unsupported HTML documentation attribute: " + name)
            if (tag, name) in {("a", "href"), ("img", "src")}:
                policy.require(type(value) is str and bool(value) and value == value.strip()
                               and not any(ord(char) < 32 or ord(char) == 127 for char in value), "invalid HTML documentation target")
                self.targets.append(value)
            elif name == "id" or (tag == "a" and name == "name"):
                policy.require(type(value) is str and bool(value), "invalid HTML documentation anchor")
                self.anchors.add(value)

    def handle_endtag(self, tag):
        policy.require(tag in self.TAGS, "unsupported HTML documentation tag: " + tag)

    def handle_data(self, data):
        policy.require(re.search(r"<\s*/?\s*[A-Za-z!?]", data) is None, "unsupported or incomplete HTML documentation syntax")

    def handle_decl(self, decl):
        raise policy.PolicyError("unsupported HTML documentation declaration")

    def handle_pi(self, data):
        raise policy.PolicyError("unsupported HTML documentation processing instruction")

    def unknown_decl(self, data):
        raise policy.PolicyError("unsupported HTML documentation declaration")


def _html_references(visible):
    parser = _DocumentationHTML()
    parser.feed(visible); parser.close()
    return parser.targets, parser.anchors


def _anchors(text):
    visible, headings = _documentation_lex(text)
    _, anchors = _html_references(visible)
    used = {}
    for heading in headings:
        title = _heading_text(heading).lower()
        title = re.sub(r"[^\w\-\s]", "", title).replace(" ", "-")
        count = used.get(title, 0); used[title] = count + 1
        anchors.add(title + ("-" + str(count) if count else ""))
    return anchors


def inspect_documentation(candidate, check, root=ROOT, *, tested_sha=None):
    tested = _tested_checkout(root, candidate, tested_sha)
    return _inspect_documentation(candidate, check, root, tested)


def _inspect_documentation(candidate, check, root, tested):
    if not candidate["changed_files"]:
        policy.require(check in CHECKS, "unknown documentation check")
        # An empty documentation subset is an explicit PR scope result; the
        # child still validates Git identity and performs the committed diff.
        if check == "diff":
            command = ["git", "diff", "--check", candidate["baseline"], tested["head"]]
            result = subprocess.run(command, cwd=root, capture_output=True, check=True)
            return {"inspected_files": 0, "tested_checkout": tested, "results": [{"command": command,
                    "stdout": result.stdout.decode("utf-8"), "stderr": result.stderr.decode("utf-8")} ]}
        return {"inspected_files": 0, "tested_checkout": tested, "files" if check == "bytes" else "local_links": []}
    documents = _markdown(root, candidate, tested["head"])
    files = [{"path": name, "sha256": digest(raw), "size": len(raw)} for name, (raw, _) in documents.items()]
    if check == "bytes":
        return {"files": files, "inspected_files": len(files), "tested_checkout": tested}
    if check == "diff":
        results = []
        for end in (candidate["head"], tested["head"]):
            command = ["git", "diff", "--check", candidate["baseline"], end, "--", *candidate["changed_files"]]
            result = subprocess.run(command, cwd=root, capture_output=True, check=False)
            policy.require(result.returncode == 0, "documentation diff check failed: " + result.stdout.decode("utf-8", errors="replace"))
            results.append({"command": command, "stdout": result.stdout.decode("utf-8"), "stderr": result.stderr.decode("utf-8")})
        return {"results": results, "inspected_files": len(files), "tested_checkout": tested}
    policy.require(check == "local_links", "unknown documentation check")
    checked = []
    for name, (_, text) in documents.items():
        visible = _visible(text)
        definitions = dict(re.findall(r"^ {0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?", visible, re.M))
        targets = [a or b for a, b in re.findall(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)", visible)]
        html_targets, _ = _html_references(visible)
        targets += html_targets
        for label in re.findall(r"!?\[[^\]]+\]\[([^\]]+)\]", visible):
            policy.require(label in definitions, "missing Markdown reference definition: " + label)
            targets.append(definitions[label])
        targets += list(definitions.values())
        for target in targets:
            split = urlsplit(target)
            if split.scheme or split.netloc:
                policy.require(split.scheme in {"http", "https", "mailto"} or target.startswith("//"), "unsupported documentation link scheme")
                continue
            policy.require(not split.query, "local documentation link query is unsupported")
            path = unquote(split.path)
            policy.require("\\" not in path and not path.startswith("/"), "non-relative local documentation link")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), path)) if path else name
            policy.require(resolved not in {".", ".."} and not resolved.startswith("../"), "documentation link escapes repository")
            try:
                kind = git(root, "cat-file", "-t", tested["head"] + ":" + resolved).decode("ascii").strip()
            except subprocess.CalledProcessError as error:
                raise policy.PolicyError("missing local documentation target: " + resolved) from error
            if kind == "tree":
                policy.require(not split.fragment, "local directory anchors require another checker")
                tree = git(root, "rev-parse", tested["head"] + ":" + resolved).decode("ascii").strip()
                policy.sha(tree, "local directory tree")
                content = git(root, "cat-file", "tree", tree)
                checked.append({"source": name, "target": target, "path": resolved, "git_type": "tree",
                                "tree": tree, "sha256": digest(content)})
                continue
            policy.require(kind == "blob", "unsupported local documentation Git object type: " + kind)
            content = git(root, "cat-file", "blob", tested["head"] + ":" + resolved)
            if split.fragment:
                policy.require(PurePosixPath(resolved).suffix.lower() == ".md", "local non-Markdown anchor requires another checker")
                policy.require(unquote(split.fragment) in _anchors(content.decode("utf-8")), "missing local documentation anchor: " + target)
            checked.append({"source": name, "target": target, "path": resolved, "sha256": digest(content)})
    return {"inspected_files": len(files), "local_links": checked, "tested_checkout": tested}


def _reference(parent, path):
    return {"path": path.relative_to(parent).as_posix(), "sha256": digest(path.read_bytes())}


def _artifact(parent, pointer):
    policy.fields(pointer, "path sha256", "documentation artifact")
    policy.sha(pointer["sha256"], "artifact.sha256", 64)
    name = pointer["path"]
    policy.require(type(name) is str and bool(name) and "\\" not in name and ":" not in name
                   and not PurePosixPath(name).is_absolute() and PurePosixPath(name).as_posix() == name
                   and all(part not in {".", ".."} for part in PurePosixPath(name).parts), "invalid documentation artifact path")
    path = Path(parent) / name
    policy.require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(Path(parent).resolve()),
                   "documentation artifact escapes evidence root")
    policy.require(digest(path.read_bytes()) == pointer["sha256"], "documentation artifact hash mismatch")
    return path


def run_documentation_checks(candidate, binding, output, root=ROOT):
    """Execute three real stdlib/Git checks, retaining failures and raw streams."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = output.parent / "candidate.json"
    write(candidate_path, candidate)
    tested = _bound_checkout(root, candidate, binding["checkout"], current=True)
    with (output.parent / "tested-commit.raw").open("xb") as stream:
        stream.write(git(root, "cat-file", "commit", tested["head"]))
    checks = []
    for name in CHECKS:
        command = [sys.executable, str(Path(__file__).resolve()), "inspect", "--candidate", str(candidate_path.resolve()), "--check", name,
                   "--tested-sha", tested["head"]]
        result = subprocess.run(command, cwd=root, capture_output=True, check=False)
        stdout, stderr = output.parent / (name + ".stdout.json"), output.parent / (name + ".stderr.log")
        with stdout.open("xb") as stream: stream.write(result.stdout)
        with stderr.open("xb") as stream: stream.write(result.stderr)
        checks.append({"name": name, "command": ["<python>", "scripts/pr_flow_ci.py", "inspect", "--candidate", "candidate.json", "--check", name,
                                                   "--tested-sha", tested["head"]],
                       "exit_code": result.returncode, "stdout": _reference(output.parent, stdout), "stderr": _reference(output.parent, stderr)})
    record = {"schema": SCHEMA, "policy": policy.POLICY_ID, "candidate": candidate,
              "status": "success" if all(item["exit_code"] == 0 for item in checks) else "failed",
              "execution": {"python": platform.python_version(), "platform": platform.system(),
                            "checker_sha256": digest(Path(__file__).read_bytes())},
              "candidate_original": _reference(output.parent, candidate_path), "checks": checks, **binding}
    # Retain failed evidence when the checkout drifts while a child runs.
    # Immutable object reads stay bound to the original tested SHA.
    try:
        _bound_checkout(root, candidate, binding["checkout"], current=True)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        record["status"] = "failed"
        write(output, record)
        raise
    write(output, record)
    return record


def run_pr_documentation_checks(event, environment, output, root=ROOT):
    """Run neutral supplemental checks on every PR, including full fallback."""
    scope, binding = pr_execution_scope(event, environment, root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    original = output.parent / "scope.json"
    write(original, scope)
    checks = []
    tested_sha = binding["checkout"]["checkout_sha"]
    tested_commit = output.parent / "tested-commit.raw"
    with tested_commit.open("xb") as stream:
        stream.write(git(root, "cat-file", "commit", tested_sha))
    for name in CHECKS:
        command = [sys.executable, str(Path(__file__).resolve()), "inspect-pr", "--scope", str(original.resolve()),
                   "--check", name, "--tested-sha", tested_sha]
        result = subprocess.run(command, cwd=root, capture_output=True, check=False)
        stdout, stderr = output.parent / (name + ".stdout.json"), output.parent / (name + ".stderr.log")
        stdout.write_bytes(result.stdout); stderr.write_bytes(result.stderr)
        checks.append({"name": name, "command": ["<python>", "scripts/pr_flow_ci.py", "inspect-pr", "--scope", "scope.json",
                        "--check", name, "--tested-sha", tested_sha], "exit_code": result.returncode,
                       "stdout": _reference(output.parent, stdout), "stderr": _reference(output.parent, stderr)})
    record = {"schema": PR_SCHEMA, "policy": policy.POLICY_ID, "execution_scope": scope,
              "status": "success" if all(item["exit_code"] == 0 for item in checks) else "failed",
              "execution": {"python": platform.python_version(), "platform": platform.system(),
                            "checker_sha256": digest(Path(__file__).read_bytes())},
              "scope_original": _reference(output.parent, original), "tested_commit": _reference(output.parent, tested_commit),
              "checks": checks, **binding}
    try:
        policy.require(pr_execution_scope(event, environment, root) == (scope, binding), "PR scope changed during supplemental checks")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        record.update(status="failed", error=str(error))
    write(output, record)
    return record


def verify_pr_documentation_checks(path, candidate=None, *, _root=None):
    """Prove applicability of an original /1 or neutral PR-execution /2 record."""
    path = Path(path)
    record = load(path)
    if record.get("schema") == SCHEMA:
        policy.require(candidate is not None, "task-bound documentation requires its exact candidate")
        return verify_documentation_checks(path, candidate, _root=_root)
    policy.fields(record, "schema policy execution_scope status execution scope_original tested_commit checks ci checkout", "supplemental documentation")
    policy.require(record["schema"] == PR_SCHEMA and record["policy"] == policy.POLICY_ID and record["status"] == "success",
                   "unknown or unsuccessful supplemental documentation")
    policy.fields(record["ci"], "run_id attempt job event", "supplemental CI")
    policy.require(all(type(record["ci"][key]) is int and record["ci"][key] > 0 for key in ("run_id", "attempt"))
                   and record["ci"]["job"] == "test" and record["ci"]["event"] == "pull_request", "invalid supplemental CI identity")
    policy.fields(record["checkout"], "number repository base_repository head_repository base_ref head_ref base_sha head_sha checkout_sha checkout_tree checkout_parents",
                  "supplemental checkout")
    policy.require(type(record["checkout"]["number"]) is int and record["checkout"]["number"] > 0
                   and record["checkout"]["base_ref"] in {"develop", "main"}, "invalid supplemental PR identity")
    policy.text(record["checkout"]["head_ref"], "supplemental head branch")
    policy.text(record["checkout"]["head_repository"], "supplemental head repository")
    root = ROOT if _root is None else _root
    scope = record["execution_scope"]
    tested = _pr_scope_checkout(scope, record["checkout"]["checkout_sha"], root)
    policy.require(raw_commit_identity(_artifact(path.parent, record["tested_commit"]).read_bytes()) == tested,
                   "supplemental tested commit original differs")
    checkout = record["checkout"]
    policy.require((checkout["repository"], checkout["base_repository"], checkout["base_sha"], checkout["head_sha"],
                    checkout["checkout_tree"], checkout["checkout_parents"]) ==
                   (scope["repository"], scope["repository"], scope["base"], scope["head"], tested["tree"], tested["parents"]),
                   "supplemental checkout scope differs")
    if candidate is not None:
        bind_git(candidate, root)
        policy.require((scope["repository"], scope["base"], scope["head"]) ==
                       (candidate["repository"], candidate["base"], candidate["head"]), "supplemental scope is from another PR candidate")
        required = set(document_paths(candidate["changed_files"]))
        policy.require(required <= set(document_paths(scope["changed_files"])), "supplemental evidence omits required task documentation")
        policy.require(tested == _bound_checkout(root, candidate, checkout), "supplemental tested integration differs")
    policy.require(record["execution"] == {"python": "3.13.0", "platform": "Windows", "checker_sha256": digest(Path(__file__).read_bytes())},
                   "unsupported or changed supplemental checker environment")
    policy.require(load(_artifact(path.parent, record["scope_original"])) == scope, "PR execution scope original differs")
    policy.require(type(record["checks"]) is list and [item["name"] for item in record["checks"]] == list(CHECKS),
                   "missing/duplicate supplemental documentation check")
    for item in record["checks"]:
        policy.fields(item, "name command exit_code stdout stderr", "supplemental check")
        policy.require(type(item["exit_code"]) is int and item["exit_code"] == 0 and item["command"] ==
                       ["<python>", "scripts/pr_flow_ci.py", "inspect-pr", "--scope", "scope.json", "--check", item["name"],
                        "--tested-sha", tested["head"]], "failed or changed supplemental documentation command")
        actual = load(_artifact(path.parent, item["stdout"]))
        policy.require(type(actual) is dict and type(actual.get("inspected_files")) is int
                       and actual["inspected_files"] == len(document_paths(scope["changed_files"])), "supplemental inspected scope differs")
        policy.require(not _artifact(path.parent, item["stderr"]).read_bytes().strip(), "supplemental checker emitted errors")
        policy.require(actual == inspect_pr_documentation(scope, item["name"], root, tested_sha=tested["head"]),
                       "supplemental documentation differs from actual Git bytes/links/diff")
    return record


def verify_documentation_checks(path, candidate, *, _root=None):
    """Verify retained, nonempty results; caller also verifies current PR/run."""
    path = Path(path); record = load(path)
    policy.fields(record, "schema policy candidate status execution candidate_original checks ci checkout", "documentation checks")
    policy.require(record["schema"] == SCHEMA and record["policy"] == policy.POLICY_ID and record["status"] == "success"
                   and record["candidate"] == candidate, "unsuccessful or stale documentation checks")
    policy.validate_candidate(candidate)
    root = ROOT if _root is None else _root
    tested = _bound_checkout(root, candidate, record["checkout"])
    execution = record["execution"]
    policy.fields(execution, "python platform checker_sha256", "documentation execution")
    policy.require(execution == {"python": "3.13.0", "platform": "Windows", "checker_sha256": digest(Path(__file__).read_bytes())},
                   "unsupported or changed documentation checker environment")
    policy.require(load(_artifact(path.parent, record["candidate_original"])) == candidate, "candidate original differs")
    policy.require(type(record["checks"]) is list and [item["name"] for item in record["checks"]] == list(CHECKS),
                   "missing, duplicate or empty required documentation checks")
    for item in record["checks"]:
        policy.fields(item, "name command exit_code stdout stderr", "documentation check")
        policy.require(type(item["exit_code"]) is int and item["exit_code"] == 0 and item["command"] ==
                       ["<python>", "scripts/pr_flow_ci.py", "inspect", "--candidate", "candidate.json", "--check", item["name"],
                        "--tested-sha", tested["head"]],
                       "failed or changed documentation command")
        actual = load(_artifact(path.parent, item["stdout"]))
        policy.require(type(actual) is dict and actual.get("inspected_files") == len(candidate["changed_files"])
                       and type(actual["inspected_files"]) is int and actual["inspected_files"] > 0,
                       "documentation check did not inspect the actual nonempty candidate")
        policy.require(actual.get("tested_checkout") == tested, "raw documentation tested identity differs")
        policy.require(not _artifact(path.parent, item["stderr"]).read_bytes().strip(), "documentation command emitted errors")
        # Recompute these bounded checks from the same immutable Git objects;
        # a hand-written empty success log is never enough.
        policy.require(actual == inspect_documentation(candidate, item["name"], root, tested_sha=tested["head"]), "documentation result differs from actual candidate bytes/links/diff")
    return record


def verify_documentation_ci(ci, pr, candidate, record, *, checks_route="docs"):
    """Bind document evidence to the current original successful Actions run."""
    policy.require(ci is not None and pr is not None, "documentation checks require current PR CI")
    policy.require((ci["workflow"], ci["event"], ci["branch"], ci["head_sha"], ci["parents"])
                   == (".github/workflows/ci.yml", "pull_request", pr["head_branch"], candidate["head"], [candidate["base"], candidate["head"]]),
                   "documentation CI scope differs")
    policy.require(ci["status"] == "completed" and ci["conclusion"] == "success" and
                   (ci["run_id"], ci["attempt"]) == (ci["latest_run_id"], ci["latest_attempt"]), "documentation CI is unsuccessful or superseded")
    policy.require(record["ci"] == {"run_id": ci["run_id"], "attempt": ci["attempt"], "job": "test", "event": "pull_request"},
                   "documentation checks refer to another CI run/attempt")
    checkout = record["checkout"]
    policy.fields(checkout, "number repository base_repository head_repository base_ref head_ref base_sha head_sha checkout_sha checkout_tree checkout_parents", "documentation checkout")
    policy.require(checkout["number"] == pr["number"] and checkout["head_ref"] == pr["head_branch"] and
                   checkout["base_ref"] == pr["base_branch"] and checkout["repository"] == checkout["base_repository"] == candidate["repository"] and
                   (checkout["base_sha"], checkout["head_sha"], checkout["checkout_sha"], checkout["checkout_tree"], checkout["checkout_parents"])
                   == (candidate["base"], candidate["head"], ci["tested_sha"], ci["tree"], ci["parents"]), "documentation checkout differs from current PR integration")
    policy.require(ci["tested_sha"] not in ci["parents"], "documentation requires synthetic merge validation")
    jobs = {job["name"]: job for job in ci["jobs"]}
    summary = jobs.get("Python 3.13")
    policy.require(summary is not None and summary["runner"].startswith("windows-") and summary["python_version"] == "3.13.0", "missing supported documentation CI job")
    for job in ci["jobs"]:
        if job["name"] == "Merge short validation" and job["conclusion"] == "skipped": continue
        policy.require(job["name"] == "Python 3.13" and job["conclusion"] == "success" and
                       (job["run_id"], job["attempt"], job["tested_sha"]) == (ci["run_id"], ci["attempt"], ci["tested_sha"]), "documentation CI jobs are mixed or unsuccessful")
    required = ("Check out repository", "Set up Python", "Show Python version", "Resolve current validation route", *DOC_STEPS, "Verify required validation results")
    policy.require(all(summary["steps"].get(name) == "success" for name in required), "required documentation CI step missing or unsuccessful")
    policy.require(checks_route in {"docs", "docs_fallback_full", "full"}, "unknown documentation checks route")
    if checks_route == "docs":
        policy.require(all(summary["steps"].get(name) == "skipped" for name in FULL_STEPS), "documentation CI mixes full and document results")
    else:
        policy.require(all(summary["steps"].get(name) == ("skipped" if name == "Check committed whitespace (push)" else "success")
                           for name in FULL_STEPS), "full documentation fallback is missing required full results")


def verify_full_plan_scope(plan, coverage, candidate, documentation_record=None):
    """Keep the retained inactive shape finite; active PRs require raw checks."""
    if "execution_scope" not in plan:
        policy.fields(plan, "schema policy route reason", "retained inactive full plan")
        policy.require(plan["reason"] == "policy-inactive" and not document_paths(candidate["changed_files"]),
                       "unknown/full documentation producer lacks an active PR execution scope")
        return
    required = {"schema", "policy", "route", "checks_route", "reason", "execution_scope", "ci", "checkout"}
    policy.require(required <= set(plan) <= required | {"candidate", "classification_tier", "source"}, "unknown active full producer plan fields")
    policy.require(plan["ci"] == coverage["ci"] and plan["checkout"].get("checkout_sha") == coverage["candidate"]["head"]
                   and plan["checkout"].get("checkout_tree") == coverage["candidate"]["tree"]
                   and plan["checkout"].get("checkout_parents") == coverage["candidate"]["parents"],
                   "active producer plan integration/run binding differs")
    policy.require(documentation_record is not None and documentation_record.get("schema") == PR_SCHEMA,
                   "active full producer requires neutral PR-execution check originals, including an empty documentation subset")
    policy.require(documentation_record.get("execution_scope") == plan["execution_scope"] and documentation_record["ci"] == coverage["ci"]
                   and documentation_record["checkout"] == plan["checkout"], "active producer and neutral check scopes/identity differ")


def verify_pr_checks(ci, pr, candidate, checks_route, *, coverage_path=None, documentation_path=None):
    """Verify real checks independently of classification and review identity."""
    policy.require(checks_route in {"full", "docs", "docs_fallback_full"}, "unknown PR checks route")
    policy.require(ci is not None and pr is not None, "missing current PR CI")
    plan_path = (Path(coverage_path).parent / "pr-flow-ci/plan.json" if checks_route != "docs" and coverage_path is not None
                 else Path(documentation_path).parent.parent / "plan.json" if documentation_path is not None else None)
    policy.require(plan_path is not None and plan_path.is_file(), "missing original producer validation plan")
    plan = load(plan_path)
    policy.require(plan.get("schema") == "pr-flow-ci-plan/1" and plan.get("policy") == policy.POLICY_ID,
                   "producer plan has an unknown policy/schema")
    policy.require(plan.get("route") == ("docs" if checks_route == "docs" else "full"), "actual producer route differs from required checks")
    if "candidate" in plan:
        policy.require(plan["candidate"] == candidate, "producer plan belongs to another task/candidate")
    if checks_route == "docs":
        policy.require(plan.get("candidate") == candidate and plan.get("classification_tier") == "docs"
                       and plan.get("checks_route") == "docs" and type(plan.get("source")) is dict,
                       "documentation exemption lacks its coordinator-attested producer source")
        from scripts.pr_flow_transport import verify_retained_capture
        checkout = load(documentation_path)["checkout"]
        event = {"pull_request": {"number": checkout["number"],
                 "base": {"sha": checkout["base_sha"], "ref": checkout["base_ref"], "repo": {"full_name": checkout["base_repository"]}},
                 "head": {"sha": checkout["head_sha"], "ref": checkout["head_ref"], "repo": {"full_name": checkout["head_repository"]}}}}
        transfer = verify_retained_capture(plan["source"], event, plan_path.parent / "transport", ROOT, candidate=candidate)
        from scripts.pr_review_gate import validate_tiered_reviews
        captured = transfer["capture"]
        reviewed = validate_tiered_reviews(candidate, captured["classification"], captured["implementers"], captured["review_originals"],
                                          original_resolver=transfer["original_resolver"])
        policy.require(reviewed["code_ready"] and reviewed["classification"]["confirmed"] and reviewed["classification"]["tier"] == "docs",
                       "producer capture does not prove the required documentation review")
    if checks_route == "docs_fallback_full":
        policy.require(bool(document_paths(candidate["changed_files"])) and not candidate["risks"]
                       and len(document_paths(candidate["changed_files"])) == len(candidate["changed_files"]),
                       "full fallback cannot reclassify executable/core changes as documentation")
    if checks_route != "docs":
        from scripts.pr_review_gate import _current_ci_problem
        from scripts.validation_evidence import verify_current_coverage
        problem = _current_ci_problem(ci, "pull_request", pr["head_branch"], candidate["head"], [candidate["base"], candidate["head"]])
        policy.require(problem is None, problem or "invalid full CI")
        summary = next(job for job in ci["jobs"] if job["name"] == "Python 3.13")
        policy.require(summary["steps"].get("Resolve current validation route") == "success",
                       "full producer route resolution is missing/skipped/unsuccessful")
        policy.require(coverage_path is not None, "missing full current coverage")
        coverage = verify_current_coverage(coverage_path)
        policy.require(coverage["candidate"] == {"head": ci["tested_sha"], "tree": ci["tree"], "parents": ci["parents"]}
                       and coverage["ci"] == {"run_id": ci["run_id"], "attempt": ci["attempt"], "job": "test", "event": "pull_request"}
                       and coverage["checkout"]["number"] == pr["number"] and coverage["checkout"]["head_ref"] == pr["head_branch"]
                       and coverage["checkout"]["base_ref"] == pr["base_branch"]
                       and coverage["checkout"]["base_sha"] == candidate["base"] and coverage["checkout"]["head_sha"] == candidate["head"],
                       "full coverage is from another PR/run/attempt/integration")
        verify_full_plan_scope(plan, coverage, candidate, load(documentation_path) if documentation_path is not None else None)
    if checks_route in {"docs", "docs_fallback_full"} or "execution_scope" in plan or document_paths(candidate["changed_files"]) or documentation_path is not None:
        policy.require(documentation_path is not None, "missing required supplemental documentation checks")
        record = verify_pr_documentation_checks(documentation_path, candidate)
        verify_documentation_ci(ci, pr, candidate, record, checks_route=checks_route)
    return {"source_route": checks_route, "candidate": {"head": ci["tested_sha"], "tree": ci["tree"], "parents": ci["parents"]},
            "ci": {"run_id": ci["run_id"], "attempt": ci["attempt"], "job": "test", "event": "pull_request"}}


def verify_tiered_post_merge(path, candidate, checks_route, pr_ci, *, coverage_path=None, documentation_path=None):
    from scripts.pr_flow_evidence import verify_tiered_post_merge as verify
    return verify(path, candidate, checks_route, pr_ci, coverage_path=coverage_path, documentation_path=documentation_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    route = commands.add_parser("dispatch"); route.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("documentation"); run.add_argument("--plan", type=Path, required=True); run.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-documentation"); verify.add_argument("path", type=Path)
    supplemental = commands.add_parser("documentation-pr"); supplemental.add_argument("--output", type=Path, required=True)
    supplemental_verify = commands.add_parser("verify-pr-documentation"); supplemental_verify.add_argument("path", type=Path)
    inspect = commands.add_parser("inspect"); inspect.add_argument("--candidate", type=Path, required=True); inspect.add_argument("--check", choices=CHECKS, required=True)
    inspect.add_argument("--tested-sha")
    inspect_pr = commands.add_parser("inspect-pr"); inspect_pr.add_argument("--scope", type=Path, required=True)
    inspect_pr.add_argument("--check", choices=CHECKS, required=True); inspect_pr.add_argument("--tested-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            # Read-only bounded checker; supplies no route or merge permission.
            result = inspect_documentation(load(args.candidate), args.check, Path.cwd(), tested_sha=args.tested_sha)
        elif args.command == "inspect-pr":
            result = inspect_pr_documentation(load(args.scope), args.check, Path.cwd(), tested_sha=args.tested_sha)
        elif args.command in {"documentation-pr", "verify-pr-documentation"}:
            policy.require(policy.ENABLED, "tiered supplemental documentation dispatch is inactive")
            event = load(os.environ["GITHUB_EVENT_PATH"])
            if args.command == "documentation-pr":
                result = run_pr_documentation_checks(event, os.environ, args.output, Path.cwd())
            else:
                result = verify_pr_documentation_checks(args.path, _root=Path.cwd())
                scope, binding = pr_execution_scope(event, os.environ, Path.cwd())
                policy.require(result["execution_scope"] == scope and all(result[key] == value for key, value in binding.items()),
                               "supplemental record is from another current runner event")
            policy.require(result["status"] == "success", "supplemental documentation checks failed; originals retained")
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                    stream.write("check_count=" + str(len(result["checks"])) + "\n")
        elif args.command == "verify-documentation":
            policy.require(policy.ENABLED, "tiered documentation dispatch is inactive")
            record = load(args.path); result = verify_documentation_checks(args.path, record["candidate"])
        else:
            event = load(os.environ["GITHUB_EVENT_PATH"])
            result = dispatch(event, os.environ)
            if args.command == "documentation":
                policy.require(policy.ENABLED and result["route"] == "docs" and result == load(args.plan), "inactive or stale documentation plan")
                result = run_documentation_checks(result["candidate"], {key: result[key] for key in ("ci", "checkout")}, args.output)
                policy.require(result["status"] == "success", "documentation checks failed; original results retained")
            else:
                write(args.output, result)
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                    if args.command == "dispatch": stream.write("route=" + result["route"] + "\nchecks_route=" + result.get("checks_route", result["route"]) + "\npolicy_active=" + str(policy.ENABLED).lower() + "\n")
                    else: stream.write("check_count=" + str(len(result["checks"])) + "\n")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
