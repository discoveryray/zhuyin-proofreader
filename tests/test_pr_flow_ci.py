"""Synthetic local evidence only; no product suite, GUI, remote or installs."""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import pr_flow_ci as ci
from scripts import pr_flow_policy as policy


def documentation_fixture(folder, text="# Guide\n\n[Target](target.md#details)\n", *, assets=None):
    """Create an isolated synthetic Git object graph, never modify task Git."""
    root = Path(folder)
    def git(*args, input=None):
        result = subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True)
        return result.stdout.decode().strip()
    git("init", "-b", "synthetic")
    git("config", "user.name", "Synthetic fixture")
    git("config", "user.email", "synthetic@example.invalid")
    (root / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "target.md").write_text("# Target\n\n## Details\n", encoding="utf-8")
    for name, raw in (assets or {}).items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    git("add", "guide.md", "target.md", *(name for name in (assets or {}) if name not in {"guide.md", "target.md"}))
    git("commit", "-m", "synthetic baseline")
    baseline = git("rev-parse", "HEAD")
    (root / "guide.md").write_text(text, encoding="utf-8")
    git("add", "guide.md"); git("commit", "-m", "synthetic documentation candidate")
    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    merge = git("commit-tree", tree, "-p", baseline, "-p", head, input=b"synthetic integration\n")
    git("update-ref", "refs/heads/synthetic", merge)
    candidate = dict(task_id="synthetic-ci", repository="discoveryray/zhuyin-proofreader", baseline=baseline,
                     base=baseline, merge_base=baseline, head=head, tree=tree, scope="synthetic complete task diff",
                     changed_files=["guide.md"], risks=[])
    event = {"pull_request": {"number": 123, "base": {"sha": baseline, "ref": "develop", "repo": {"full_name": candidate["repository"]}},
                              "head": {"sha": head, "ref": "synthetic/docs", "repo": {"full_name": candidate["repository"]}}}}
    environment = dict(GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/123/merge", GITHUB_SHA=merge,
                       GITHUB_REPOSITORY=candidate["repository"], GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="test")
    return candidate, event, environment


def review_capture(root, candidate, tier="docs", count=1):
    pointers = []
    for number in range(count):
        ref = "synthetic://review/" + str(number)
        report = dict(schema="pr-flow-code-review/1", policy=policy.POLICY_ID, report_ref=ref,
                      candidate=deepcopy(candidate), reviewer="independent-reviewer-" + str(number), session="independent-session-" + str(number),
                      independent=True, confirmed_tier=tier, mode="full",
                      reviewed_range={"from_head": candidate["baseline"], "to_head": candidate["head"]},
                      previous_report_ref=None, full_review_ref=ref, delta_from=None,
                      impact_known=True, architecture_changed=False, integration_reviewed=True,
                      impact_summary="Synthetic complete diff and Markdown consumer review.", review_checks=[],
                      verdict="PASS", findings=[], resolutions=[])
        path = root / ("review-" + str(number) + ".json")
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        pointers.append(dict(report_ref=ref, source_file=str(path.resolve()), sha256=ci.digest(path.read_bytes()), backups=[]))
    return dict(schema="pr-flow-ci-capture/1", policy=policy.POLICY_ID,
                coordinator=dict(actor="synthetic-coordinator", source_refs=["synthetic://independently-captured-originals"],
                                 authorization_ref="synthetic://authorized-task", independent_sources_checked=True),
                candidate=deepcopy(candidate), classification=dict(tier=tier, author="implementer", rationale="Synthetic bounded known consumers; review checks are represented by this local fixture.",
                                                                  known_consumers=["Markdown renderer"], impact_known=True),
                implementers=["implementer"], review_originals=pointers)


def synthetic_ci(candidate, binding):
    steps = {name: "skipped" for name in ci.FULL_STEPS}
    steps.update({name: "success" for name in ("Check out repository", "Set up Python", "Show Python version", "Resolve current validation route", *ci.DOC_STEPS, "Verify required validation results")})
    job = dict(name="Python 3.13", conclusion="success", runner="windows-latest", python_version="3.13.0",
               run_id=1234, attempt=1, tested_sha=binding["checkout"]["checkout_sha"], steps=steps)
    value = dict(workflow=".github/workflows/ci.yml", event="pull_request", branch="synthetic/docs", head_sha=candidate["head"],
                 parents=[candidate["base"], candidate["head"]], tested_sha=job["tested_sha"], tree=binding["checkout"]["checkout_tree"],
                 run_id=1234, attempt=1, latest_run_id=1234, latest_attempt=1, status="completed", conclusion="success", jobs=[job])
    pr = dict(number=123, head_branch="synthetic/docs", base_branch="develop")
    return value, pr


def diverged_documentation_fixture(folder, change):
    """Keep feature scope fixed while testing a distinct current base/merge."""
    candidate, event, environment = documentation_fixture(folder)
    root = Path(folder)
    def git(*args, input=None):
        return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True).stdout.decode().strip()
    def tree(files):
        entries = []
        for name, raw in sorted(files.items()):
            blob = git("hash-object", "-w", "--stdin", input=raw)
            entries.append("100644 blob " + blob + "\t" + name + "\n")
        return git("mktree", input="".join(entries).encode())
    base_files = {"guide.md": b"# Guide\n", "target.md": b"# Target\n\n## Details\n"}
    merge_files = {**base_files, "guide.md": b"# Guide\n\n[Target](target.md#details)\n"}
    if change == "missing_target":
        del base_files["target.md"]; del merge_files["target.md"]
    elif change == "missing_anchor":
        base_files["target.md"] = merge_files["target.md"] = b"# Target\n\n## Renamed\n"
    elif change in {"guide_bytes", "whitespace"}:
        prefix = b"Base note  \n\n" if change == "whitespace" else b"Base note\n\n"
        base_files["guide.md"] = prefix + base_files["guide.md"]
        merge_files["guide.md"] = prefix + merge_files["guide.md"]
    else:
        raise AssertionError(change)
    base = git("commit-tree", tree(base_files), "-p", candidate["baseline"], input=b"synthetic divergent base\n")
    merged = git("commit-tree", tree(merge_files), "-p", base, "-p", candidate["head"], input=b"synthetic tested integration\n")
    git("update-ref", "refs/heads/synthetic", merged)
    candidate["base"] = base
    event["pull_request"]["base"]["sha"] = base
    environment["GITHUB_SHA"] = merged
    return candidate, event, environment


class PrFlowCiTests(unittest.TestCase):
    def html_checks(self, text, *, assets=None, succeeds=True, error=None):
        """Run three actual child CLIs and the real bounded artifact verifier."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate, event, environment = documentation_fixture(root, text, assets=assets)
            binding = ci.bind_event(candidate, event, environment, root)
            output = root / "checks/checks.json"
            record = ci.run_documentation_checks(candidate, binding, output, root)
            self.assertEqual([item["name"] for item in record["checks"]], list(ci.CHECKS))
            self.assertEqual(record["status"], "success" if succeeds else "failed")
            if succeeds:
                ci.verify_documentation_checks(output, candidate, _root=root)
                return ci.load(output.parent / "local_links.stdout.json")
            local = next(item for item in record["checks"] if item["name"] == "local_links")
            self.assertNotEqual(local["exit_code"], 0)
            if error:
                self.assertIn(error, (output.parent / local["stderr"]["path"]).read_text(encoding="utf-8"))
            with self.assertRaises(ValueError): ci.verify_documentation_checks(output, candidate, _root=root)

    def test_html_href_src_and_explicit_anchors_use_actual_targets_and_legal_external_schemes(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==")
        target = b'# Target\n\n## Details\n<a id="explicit&amp;anchor"></a>\n<a name="legacy"></a>\n'
        result = self.html_checks('# Guide\n\n<details open><summary>Targets</summary>\n'
            '<A HREF="target.md#details">Heading</A>\n<a href="target.md#explicit%26anchor">Entity anchor</a>\n'
            '<a href=target.md#legacy>Unquoted target</a>\n<img src="pixel.png" alt="Pixel" width="1" height="1" />\n'
            '<a href="https://example.invalid/?a=1&amp;b=2">External</a>\n'
            '<img src="//example.invalid/pixel.png" alt="External" />\n<a href="mailto:example@example.invalid">Mail</a>\n</details>\n',
            assets={"pixel.png": png, "target.md": target})
        self.assertEqual([item["target"] for item in result["local_links"]],
                         ["target.md#details", "target.md#explicit%26anchor", "target.md#legacy", "pixel.png"])

    def test_missing_html_href_src_and_anchor_are_real_cli_failures(self):
        for markup, message in (('<a href="missing.md#absent">Missing</a>', "missing local documentation target"),
                                ('<img src="missing.png">', "missing local documentation target"),
                                ('<a href="target.md#absent">Missing anchor</a>', "missing local documentation anchor"),
                                ('<a href="#example-only">Missing anchor</a>\n`<a id="example-only"></a>`', "missing local documentation anchor")):
            with self.subTest(markup=markup): self.html_checks("# Guide\n\n" + markup + "\n", succeeds=False, error=message)

    def test_html_examples_are_ignored_without_rewriting_resource_url_bytes(self):
        result = self.html_checks('# Guide\n\n````html\n```\n<a href="missing-fenced.md">Example</a>\n````\n'
            '`<img src="missing-inline.png">` and ``<a href="missing`inline.md">Example</a>``\n'
            '&lt;a href="missing-escaped.md"&gt;Example&lt;/a&gt;\n<!-- [Example](missing-comment.md) -->\n'
            '<a href="#guide">Actual anchor</a>\n')
        self.assertEqual([item["target"] for item in result["local_links"]], ["#guide"])
        self.html_checks('# Guide\n\n<a href="tar`example`get.md#details">Must preserve URL</a>\n',
                         succeeds=False, error="missing local documentation target: tar`example`get.md")
        self.html_checks('# Guide\n\n- text `foo\n    <div>\n    <a href="missing.md">Bad</a>\n    </div>\n  bar`\n',
                         succeeds=False, error="unsupported code span across Markdown/HTML blocks")
        self.html_checks('# Guide\n\n`foo\n- [broken](missing.md)\nbar`\n',
                         succeeds=False, error="unsupported code span across Markdown/HTML blocks")
        for marker in ('> quoted', '1. item', '===', '- - -', '___', '<!-- note -->', '<!DOCTYPE html>', '<?xml?>'):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "unsupported code span"):
                ci._visible('`foo\n' + marker + '\nbar`')

    def test_raw_html_blocks_cannot_hide_resources_or_create_markdown_anchors(self):
        for markup in ('<div>\n```\n<a href="missing.md">Actual HTML</a>\n```\n</div>',
                       '<pre>\n\n```\n<img src="missing.png">\n```\n</pre>'):
            with self.subTest(markup=markup):
                self.html_checks("# Guide\n\n" + markup + "\n", succeeds=False, error="unsupported raw HTML/code-fence mixture")
        self.html_checks('# Guide\n\n<div>\n`<a href="missing-raw.md">Actual HTML</a>`\n</div>\n',
                         succeeds=False, error="missing local documentation target: missing-raw.md")
        for tag in ("script", "style", "textarea"):
            with self.subTest(tag=tag):
                self.html_checks(f'# Guide\n\n<{tag}>\n\n```\n<a href="missing.md">Actual HTML</a>\n```\n</{tag}>\n',
                                 succeeds=False, error="unsupported HTML documentation tag: " + tag)
        self.html_checks('# Guide\n\n<a href="target.md#not-a-heading">Not an actual Markdown heading</a>\n',
                         assets={"target.md": b"# Target\n\n<div>\n## Not a heading\n</div>\n"},
                         succeeds=False, error="missing local documentation anchor")
        result = self.html_checks('# Guide\n\n```html\n<div>\n<script>\n<a href="missing-example.md">Example</a>\n</script>\n</div>\n```\n'
                                  '\n<!--\n```\n-->\n\n[Actual](target.md#details)\n')
        self.assertEqual([item["target"] for item in result["local_links"]], ["target.md#details"])

    def test_local_directory_targets_require_actual_git_tree_without_fragment(self):
        assets = {"nested/guide.md": b"# Nested guide\n"}
        checked = self.html_checks('# Guide\n\n[Directory](nested/)\n<a href="nested">HTML directory</a>\n', assets=assets)
        self.assertEqual([item["target"] for item in checked["local_links"]], ["nested/", "nested"])
        self.assertTrue(all(item["path"] == "nested" and item["git_type"] == "tree" for item in checked["local_links"]))
        self.assertEqual(checked["local_links"][0]["tree"], checked["local_links"][1]["tree"])
        # Independently calculate the one-entry Git tree from fixed fixture
        # bytes, rather than derive the expected identity from the checker.
        blob = b"# Nested guide\n"
        blob_id = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).digest()
        raw_tree = b"100644 guide.md\0" + blob_id
        tree_id = hashlib.sha1(b"tree " + str(len(raw_tree)).encode() + b"\0" + raw_tree).hexdigest()
        self.assertEqual(checked["local_links"][0]["tree"], tree_id)
        self.assertEqual(checked["local_links"][0]["sha256"], hashlib.sha256(raw_tree).hexdigest())
        for target, error in (("missing-directory/", "missing local documentation target"),
                              ("../outside/", "documentation link escapes repository"),
                              ("nested/#guide", "local directory anchors require another checker")):
            with self.subTest(target=target):
                self.html_checks('# Guide\n\n<a href="' + target + '">Directory</a>\n',
                                 assets=assets, succeeds=False, error=error)

    def test_only_original_atx_markers_create_plain_or_inline_code_heading_slugs(self):
        result = self.html_checks('# Guide\n\n[First](target.md#example-only)\n[Second](target.md#example-only-1)\n[Escaped](target.md#amp-title)\n',
            assets={"target.md": b"# Target\n\n## `Example` only\n## `Example` only\n## \\&amp; title\n"})
        self.assertEqual(len(result["local_links"]), 3)
        for raw, error in ((b"# Target\n\n`## Example only`\n", "missing local documentation anchor"),
                           (b"# Target\n\n\\## Example only\n", "missing local documentation anchor"),
                           (b"# Target\n\n# `<em>Example</em>`\n", "unsupported HTML-like code in heading"),
                           (b"# Target\n\n# `&lt;em&gt;Example&lt;/em&gt;`\n", "unsupported HTML-like code in heading")):
            with self.subTest(raw=raw):
                self.html_checks('# Guide\n\n[Absent](target.md#example-only)\n', assets={"target.md": raw}, succeeds=False, error=error)

    def test_indented_container_and_escaped_html_cannot_supply_anchors(self):
        for body in ('    <a id="example-only"></a>', '\t<a id="example-only"></a>',
                     '>     <a id="example-only"></a>', '- Item\n      <a id="example-only"></a>'):
            with self.subTest(body=body):
                self.html_checks('# Guide\n\n[Absent](target.md#example-only)\n',
                    assets={"target.md": ("# Target\n\n" + body + "\n").encode()}, succeeds=False,
                    error="unsupported HTML in indented/container documentation")
        for body in ('\\<a id="example-only"></a>', '&lt;a id="example-only"&gt;&lt;/a&gt;'):
            with self.subTest(body=body):
                self.html_checks('# Guide\n\n[Absent](target.md#example-only)\n',
                    assets={"target.md": ("# Target\n\n" + body + "\n").encode()}, succeeds=False,
                    error="missing local documentation anchor")

    def test_type7_standalone_tag_and_known_paragraph_context_are_distinct(self):
        result = self.html_checks('# Guide\n\n[Actual](target.md#actual)\n',
            assets={"target.md": b'# Target\n\n<span>\n<a id="actual"></a>\n</span>\n\n'})
        self.assertEqual(result["local_links"][0]["target"], "target.md#actual")
        for body in ('Paragraph\n<a>\n`<a id="example-only"></a>`\n',
                     '<span>Inline text</span>\n`<a id="example-only"></a>`\n'):
            with self.subTest(body=body):
                self.html_checks('# Guide\n\n[Absent](target.md#example-only)\n',
                    assets={"target.md": ("# Target\n\n" + body).encode()}, succeeds=False, error="missing local documentation anchor")
        self.html_checks('# Guide\n\n[Unknown](target.md#example-only)\n',
            assets={"target.md": b'# Target\n\n- Container\n<span>\n<a id="example-only"></a>\n</span>\n'},
            succeeds=False, error="unsupported HTML tag after container context")
        for tag in ('<a id=x=y>', '</a junk>'):
            with self.subTest(tag=tag):
                self.html_checks('# Guide\n\n[Unknown](target.md#example-only)\n',
                    assets={"target.md": ('# Target\n\n' + tag + '\n`<a id="example-only"></a>`\n').encode()},
                    succeeds=False, error="unsupported noncanonical HTML documentation tag")

    def test_unsafe_or_unsupported_html_resource_syntax_fails_closed(self):
        cases = [('<a href="&#46;&#46;/outside.md">Escape</a>', "escapes repository"),
                 ('<img src="/absolute.png">', "non-relative local documentation link"),
                 ('<a href="javascript:alert(1)">Unsafe</a>', "unsupported documentation link scheme"),
                 ('<img src="target.md" srcset="missing.png 2x">', "unsupported HTML documentation attribute: srcset"),
                 ('<span style="background:url(missing.png)">Unsupported</span>', "unsupported HTML documentation attribute: style"),
                 ('<iframe src="missing.md"></iframe>', "unsupported HTML documentation tag: iframe"),
                 ('<base href="https://example.invalid/">', "unsupported HTML documentation tag: base"),
                 ('<a href="target.md" href="missing.md">Duplicate</a>', "duplicate HTML documentation attribute"),
                 ('<a href="target.md#details"', "incomplete HTML documentation tag"),
                 ('<a href="target.md?query=1">Unsupported query</a>', "local documentation link query is unsupported"),
                 ('<a href="pixel.png#part">Unsupported anchor format</a>', "local non-Markdown anchor requires another checker")]
        for markup, message in cases:
            with self.subTest(markup=markup):
                self.html_checks("# Guide\n\n" + markup + "\n", assets={"pixel.png": b"synthetic existence-only target"}, succeeds=False, error=message)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.candidate, self.event, self.environment = documentation_fixture(self.root)
        self.binding = ci.bind_event(self.candidate, self.event, self.environment, self.root)

    def test_inactive_public_dispatch_ignores_author_capture_and_keeps_full_and_develop_short(self):
        capture = self.root / ci.CAPTURE
        capture.parent.mkdir(parents=True); capture.write_text('{"route":"docs"}', encoding="utf-8")
        with patch.object(ci, "load", side_effect=AssertionError("inactive must not read capture")):
            self.assertEqual(ci.dispatch(self.event, self.environment, self.root)["route"], "full")
        self.assertFalse(policy.ENABLED)
        for ref, expected in (("refs/heads/develop", "short"), ("refs/heads/main", "full")):
            self.assertEqual(ci.dispatch({}, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": ref}, self.root)["route"], expected)
        with self.assertRaises(ValueError): ci.dispatch({}, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}, self.root)

    def test_pure_plan_reads_canonical_independent_review_and_binds_exact_git_event(self):
        capture = review_capture(self.root, self.candidate)
        plan = ci.validated_documentation_plan(capture, self.event, self.environment, self.root)
        self.assertEqual(plan["route"], "docs")
        self.assertEqual(plan["checkout"]["head_sha"], self.candidate["head"])
        for field in ("base", "head"):
            changed = deepcopy(self.event); changed["pull_request"][field]["sha"] = "f" * 40
            with self.subTest(field=field), self.assertRaises(ValueError): ci.validated_documentation_plan(capture, changed, self.environment, self.root)
        for field in ("tree", "merge_base", "head"):
            changed = deepcopy(capture); changed["candidate"][field] = "f" * 40
            with self.subTest(field=field), self.assertRaises((ValueError, subprocess.SubprocessError)):
                ci.validated_documentation_plan(changed, self.event, self.environment, self.root)
        changed = deepcopy(capture); changed["candidate"]["changed_files"] += ["unlisted.md"]
        with self.assertRaises(ValueError): ci.validated_documentation_plan(changed, self.event, self.environment, self.root)
        changed = {**self.environment, "GITHUB_SHA": self.candidate["head"]}
        with self.assertRaises(ValueError): ci.validated_documentation_plan(capture, self.event, changed, self.root)

    def test_unconfirmed_stale_or_forged_original_cannot_exempt_full(self):
        capture = review_capture(self.root, self.candidate)
        no_review = deepcopy(capture); no_review["review_originals"] = []
        self.assertEqual(ci.validated_documentation_plan(no_review, self.event, self.environment, self.root)["route"], "full")
        no_provenance = deepcopy(capture); no_provenance["coordinator"]["independent_sources_checked"] = False
        with self.assertRaises(ValueError): ci.validated_documentation_plan(no_provenance, self.event, self.environment, self.root)
        original = Path(capture["review_originals"][0]["source_file"])
        raw = json.loads(original.read_text())
        for mutate in (lambda r: r.update(reviewer="implementer"), lambda r: r["candidate"].update(head="f" * 40), lambda r: r.update(independent=False)):
            changed = deepcopy(raw); mutate(changed)
            original.write_text(json.dumps(changed), encoding="utf-8")
            bad = deepcopy(capture); bad["review_originals"][0]["sha256"] = ci.digest(original.read_bytes())
            try:
                self.assertNotEqual(ci.validated_documentation_plan(bad, self.event, self.environment, self.root)["route"], "docs")
            except ValueError: pass
        # Same parsed report with different original bytes must still fail.
        original.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError): ci.validated_documentation_plan(capture, self.event, self.environment, self.root)

    def test_shared_docs_local_core_counts_and_semantic_upgrade_controls(self):
        for tier, paths, risks, count in (("docs", ["guide.md"], [], 1), ("local", ["display.py"], [], 1), ("core", ["docs/VALIDATION_POLICY.md"], ["gate_ci"], 2)):
            candidate = {**self.candidate, "changed_files": paths, "risks": risks}
            classification = dict(tier=tier, author="implementer", rationale="Synthetic known scope", known_consumers=["known consumer"], impact_known=True)
            reports = [dict(candidate=candidate, reviewer="reviewer-"+str(n), session="session-"+str(n), independent=True, confirmed_tier=tier) for n in range(count)]
            self.assertFalse(policy.validate_classification(candidate, classification, ["implementer"], reports[:-1])["confirmed"])
            result = policy.validate_classification(candidate, classification, ["implementer"], reports)
            self.assertEqual((result["confirmed"], result["reviewers_required"]), (True, count))
        for paths, risks in ((["docs/VALIDATION_POLICY.md"], []), (["guide.md", "display.py"], []), (["guide.md"], ["unknown"])):
            candidate = {**self.candidate, "changed_files": paths, "risks": risks}
            classification = dict(tier="docs", author="implementer", rationale="wrong downgrade", known_consumers=["renderer"], impact_known=True)
            with self.assertRaises(ValueError): policy.validate_classification(candidate, classification, ["implementer"], [])

    def test_real_nonempty_documentation_children_and_artifact_verifier(self):
        output = self.root / "evidence/checks.json"
        result = ci.run_documentation_checks(self.candidate, self.binding, output, self.root)
        self.assertEqual(result["status"], "success")
        self.assertEqual([item["name"] for item in result["checks"]], list(ci.CHECKS))
        verified = ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        links = ci.load(output.parent / "local_links.stdout.json")
        self.assertEqual((links["inspected_files"], len(links["local_links"])), (1, 1))
        current, pr = synthetic_ci(self.candidate, self.binding)
        ci.verify_documentation_ci(current, pr, self.candidate, verified)
        for mutate in (lambda r: r.update(checks=[]), lambda r: r.update(status="failed"), lambda r: r["candidate"].update(head="f"*40), lambda r: r["checks"][0].update(exit_code=1)):
            bad = deepcopy(result); mutate(bad); output.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError): ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        output.write_text(json.dumps(result), encoding="utf-8")
        stdout = output.parent / "bytes.stdout.json"
        stdout.write_text('{"inspected_files":1,"files":[]}', encoding="utf-8")
        forged = deepcopy(result); forged["checks"][0]["stdout"]["sha256"] = ci.digest(stdout.read_bytes())
        output.write_text(json.dumps(forged), encoding="utf-8")
        with self.assertRaises(ValueError): ci.verify_documentation_checks(output, self.candidate, _root=self.root)

    def test_broken_anchor_and_whitespace_checks_retain_actual_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate, event, environment = documentation_fixture(folder, "# Guide  \n\n[Missing](target.md#absent)\n")
            binding = ci.bind_event(candidate, event, environment, folder)
            output = Path(folder) / "evidence/checks.json"
            result = ci.run_documentation_checks(candidate, binding, output, folder)
            self.assertEqual(result["status"], "failed")
            self.assertEqual([item["exit_code"] == 0 for item in result["checks"]], [True, False, False])
            self.assertTrue((output.parent / "local_links.stderr.log").read_bytes())
            with self.assertRaises(ValueError): ci.verify_documentation_checks(output, candidate, _root=folder)

    def test_documentation_ci_rejects_failure_missing_mixed_stale_pr_and_attempt(self):
        current, pr = synthetic_ci(self.candidate, self.binding)
        record = {**self.binding}
        for mutate in (lambda v: v.update(conclusion="failure"), lambda v: v.update(latest_attempt=2), lambda v: v.update(tested_sha="f"*40),
                       lambda v: v["jobs"][0]["steps"].pop("Run required documentation checks"),
                       lambda v: v["jobs"][0]["steps"].update({"Run full pytest suite":"success"})):
            bad = deepcopy(current); mutate(bad)
            with self.assertRaises(ValueError): ci.verify_documentation_ci(bad, pr, self.candidate, record)
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, {**pr,"number":124}, self.candidate, record)
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, {**pr,"head_branch":"other"}, self.candidate, record)
        bad = deepcopy(record); bad["ci"]["attempt"] = 2
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, pr, self.candidate, bad)

    def test_public_cli_has_no_activation_bypass_and_refuses_documentation_while_inactive(self):
        for arguments in (("dispatch","--ignore-inactive"), ("documentation","--plan","missing","--output","missing")):
            result = subprocess.run([sys.executable, str(Path(ci.__file__)), *arguments], cwd=self.root, capture_output=True,
                                    env={**os.environ,"GITHUB_EVENT_PATH":str(self.root/'missing-event.json')})
            self.assertNotEqual(result.returncode, 0)

    def test_documentation_checks_use_actual_integration_bytes_links_and_diff(self):
        for change in ("missing_target", "missing_anchor", "guide_bytes", "whitespace"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                candidate, event, environment = diverged_documentation_fixture(folder, change)
                binding = ci.bind_event(candidate, event, environment, folder)
                output = Path(folder) / "evidence/checks.json"
                result = ci.run_documentation_checks(candidate, binding, output, folder)
                self.assertNotEqual(candidate["tree"], binding["checkout"]["checkout_tree"])
                actual = ci.load(output.parent / "bytes.stdout.json")
                tested = actual["tested_checkout"]
                self.assertEqual(tested["head"], binding["checkout"]["checkout_sha"])
                self.assertEqual(tested["tree"], binding["checkout"]["checkout_tree"])
                raw = ci.git(folder, "cat-file", "blob", tested["head"] + ":guide.md")
                self.assertEqual(actual["files"][0]["sha256"], ci.digest(raw))
                if change == "guide_bytes":
                    self.assertEqual(result["status"], "success")
                    self.assertNotEqual(raw, ci.git(folder, "cat-file", "blob", candidate["head"] + ":guide.md"))
                    ci.verify_documentation_checks(output, candidate, _root=folder)
                    diff = ci.load(output.parent / "diff.stdout.json")
                    self.assertEqual([entry["command"][4] for entry in diff["results"]], [candidate["head"], tested["head"]])
                else:
                    self.assertEqual(result["status"], "failed")
                    failed = "diff" if change == "whitespace" else "local_links"
                    self.assertNotEqual(next(item["exit_code"] for item in result["checks"] if item["name"] == failed), 0)
                    with self.assertRaises(ValueError): ci.verify_documentation_checks(output, candidate, _root=folder)

    def test_documentation_binding_rejects_forged_git_identity_and_producer_drift(self):
        output = self.root / "evidence/checks.json"
        record = ci.run_documentation_checks(self.candidate, self.binding, output, self.root)
        for field, value in (("checkout_sha", self.candidate["head"]), ("checkout_tree", "f"*40),
                             ("checkout_parents", [self.candidate["head"], self.candidate["base"]])):
            bad = deepcopy(record); bad["checkout"][field] = value
            output.write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        output.write_text(json.dumps(record), encoding="utf-8")
        # Verification reads the bound immutable merge object even when the
        # coordinator's current checkout is the feature candidate instead.
        subprocess.run(["git", "update-ref", "refs/heads/synthetic", self.candidate["head"]], cwd=self.root, check=True, capture_output=True)
        ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        with self.assertRaises(ValueError):
            ci.run_documentation_checks(self.candidate, self.binding, self.root / "wrong-start/checks.json", self.root)
        subprocess.run(["git", "update-ref", "refs/heads/synthetic", self.binding["checkout"]["checkout_sha"]], cwd=self.root, check=True, capture_output=True)
        original_run = ci.subprocess.run
        def drift(command, *args, **kwargs):
            result = original_run(command, *args, **kwargs)
            if "inspect" in command:
                original_run(["git", "update-ref", "refs/heads/synthetic", self.candidate["head"]], cwd=self.root, check=True, capture_output=True)
            return result
        drift_output = self.root / "drift/checks.json"
        with patch.object(ci.subprocess, "run", side_effect=drift), self.assertRaises(ValueError):
            ci.run_documentation_checks(self.candidate, self.binding, drift_output, self.root)
        self.assertTrue(drift_output.is_file(), "raw child evidence must survive producer rejection")
        self.assertEqual(ci.load(drift_output)["status"], "failed")

    def test_neutral_pr_scope_runs_without_capture_and_keeps_task_identity_separate(self):
        with patch.object(policy, "ENABLED", True):
            plan = ci.dispatch(self.event, self.environment, self.root)
        self.assertEqual((plan["route"], plan["reason"]), ("full", "trusted-source-unconfigured"))
        output = self.root / "neutral/docs/checks.json"
        record = ci.run_pr_documentation_checks(self.event, self.environment, output, self.root)
        self.assertEqual(record["schema"], ci.PR_SCHEMA)
        self.assertNotIn("candidate", record)
        self.assertNotIn("task_id", record["execution_scope"])
        self.assertEqual(ci.verify_pr_documentation_checks(output, self.candidate, _root=self.root), record)
        self.assertEqual(ci.raw_commit_identity((output.parent / "tested-commit.raw").read_bytes())["head"], self.environment["GITHUB_SHA"])
        bad = deepcopy(self.candidate); bad["base"] = "f" * 40
        with self.assertRaises((ValueError, subprocess.SubprocessError)):
            ci.verify_pr_documentation_checks(output, bad, _root=self.root)
        # The old task-bound reader still refuses a neutral execution record.
        with self.assertRaises(ValueError): ci.verify_documentation_checks(output, self.candidate, _root=self.root)

    def test_docs_full_fallback_requires_real_full_and_neutral_documentation_evidence(self):
        from test_current_validation_evidence import bundle, write, reference
        root = self.root / "full"
        coverage_path = bundle(root)
        coverage = ci.load(coverage_path)
        binding = self.binding
        identity = {"head": binding["checkout"]["checkout_sha"], "tree": binding["checkout"]["checkout_tree"],
                    "parents": binding["checkout"]["checkout_parents"]}
        checkout = {key: value for key, value in binding["checkout"].items() if key not in {"checkout_sha", "checkout_tree", "checkout_parents"}}
        coverage.update(candidate=identity, ci=binding["ci"], checkout=checkout)
        environment = ci.load(root / "environment.json"); environment.update(candidate=identity, ci=binding["ci"], checkout=checkout)
        write(root / "environment.json", environment); write(root / "event.json", {"ci": binding["ci"], "checkout": checkout})
        for name in ("environment", "event"):
            coverage["artifacts"][name] = reference(root, root / (name + ".json"))
        diff = next(item for item in coverage["checks"] if item["name"] == "diff")
        diff["command"] = ["git", "diff", "--check", self.candidate["base"] + "..." + self.candidate["head"]]
        (root / "diff.log").write_text(json.dumps({"argv": diff["command"]}) + "\nexit_code=0\n", encoding="utf-8")
        diff["log"] = reference(root, root / "diff.log"); write(coverage_path, coverage)
        with patch.object(policy, "ENABLED", True):
            producer_plan = ci.dispatch(self.event, self.environment, self.root)
        ci.write(root / "pr-flow-ci/plan.json", producer_plan)
        documentation = root / "pr-flow-ci/docs/checks.json"
        ci.run_pr_documentation_checks(self.event, self.environment, documentation, self.root)
        current, pr = synthetic_ci(self.candidate, binding)
        for name in ci.FULL_STEPS:
            current["jobs"][0]["steps"][name] = "skipped" if name == "Check committed whitespace (push)" else "success"
        with patch.object(ci, "ROOT", self.root):
            verified = ci.verify_pr_checks(current, pr, self.candidate, "docs_fallback_full", coverage_path=coverage_path,
                                           documentation_path=documentation)
            self.assertEqual(verified["source_route"], "docs_fallback_full")
            with self.assertRaises(ValueError):
                ci.verify_pr_checks(current, pr, self.candidate, "docs_fallback_full", coverage_path=coverage_path)
            with self.assertRaises(ValueError):
                ci.verify_pr_checks(current, pr, self.candidate, "docs", documentation_path=documentation)
            current["jobs"][0]["steps"]["Run full pytest suite"] = "skipped"
            with self.assertRaises(ValueError):
                ci.verify_pr_checks(current, pr, self.candidate, "docs_fallback_full", coverage_path=coverage_path, documentation_path=documentation)

    def test_active_empty_scope_requires_neutral_original_and_inactive_compatibility_is_finite(self):
        coverage = {"ci": self.binding["ci"], "candidate": {"head": self.binding["checkout"]["checkout_sha"],
                    "tree": self.binding["checkout"]["checkout_tree"], "parents": self.binding["checkout"]["checkout_parents"]}}
        pure = {**self.candidate, "changed_files": ["labels.py"]}
        inactive = {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID, "route": "full", "reason": "policy-inactive"}
        ci.verify_full_plan_scope(inactive, coverage, pure)
        with self.assertRaises(ValueError): ci.verify_full_plan_scope(inactive, coverage, self.candidate)
        with self.assertRaises(ValueError): ci.verify_full_plan_scope({**inactive, "reason": "unknown"}, coverage, pure)
        active = {**inactive, "checks_route": "full", "reason": "trusted-source-unconfigured", "execution_scope": {
                    "repository": pure["repository"], "base": pure["base"], "head": pure["head"], "merge_base": pure["merge_base"], "changed_files": ["labels.py"]},
                  "ci": self.binding["ci"], "checkout": self.binding["checkout"]}
        with self.assertRaises(ValueError): ci.verify_full_plan_scope(active, coverage, pure)

    def test_neutral_documentation_cli_stays_inactive_and_raw_scope_tampering_fails(self):
        output = self.root / "neutral/docs/checks.json"
        record = ci.run_pr_documentation_checks(self.event, self.environment, output, self.root)
        bad = deepcopy(record); bad["execution_scope"]["changed_files"] = []
        output.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError): ci.verify_pr_documentation_checks(output, self.candidate, _root=self.root)
        command = [sys.executable, str(Path(ci.__file__).resolve()), "documentation-pr", "--output", str(self.root / "public.json")]
        result = subprocess.run(command, cwd=self.root, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"inactive", result.stderr)

    def test_neutral_unsupported_format_and_deleted_markdown_remain_explicit_failures(self):
        for change in ("unsupported-rst", "deleted-markdown"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                candidate, event, environment = documentation_fixture(root)
                if change == "unsupported-rst":
                    (root / "plain.rst").write_text("Heading\n=======\n", encoding="utf-8")
                else:
                    (root / "guide.md").unlink()
                def git(*args, input=None):
                    return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True).stdout.decode().strip()
                git("add", "-A"); git("commit", "-m", "synthetic unsupported documentation")
                head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
                tested = git("commit-tree", tree, "-p", candidate["base"], "-p", head, input=b"synthetic integration\n")
                git("update-ref", "refs/heads/synthetic", tested)
                event["pull_request"]["head"]["sha"] = head; environment["GITHUB_SHA"] = tested
                output = root / "evidence/checks.json"
                record = ci.run_pr_documentation_checks(event, environment, output, root)
                self.assertEqual(record["status"], "failed")
                self.assertTrue(any(item["exit_code"] != 0 for item in record["checks"]))
                with self.assertRaises(ValueError): ci.verify_pr_documentation_checks(output, _root=root)


if __name__ == "__main__":
    unittest.main()
