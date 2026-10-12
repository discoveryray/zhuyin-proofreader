"""Finite synthetic current CI records; no full suite, history replay or GUI.

Transport and checkout/environment inputs are synthetic. All coverage, archive,
reuse and gate semantic validators run their production implementation.
"""
from copy import deepcopy
import io
import hashlib
import json
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import validation_evidence as evidence
from scripts import validation_runner as runner
from scripts import pr_review_gate as gate
from test_validation_evidence import staged_state, formalize, short_fixture
from test_pr_review_gate import BASE, HEAD, SYNTHETIC, TREE, MERGE

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def reference(root, path):
    return {"path": path.relative_to(root).as_posix(), "sha256": evidence.sha256_file(path)}


def bundle(root, *, event="pull_request", base="develop"):
    """Independent two-identity inventory, command and original-file fixture."""
    root.mkdir(parents=True, exist_ok=True)
    registry = {"schema": "zhuyin-test-group-registry/2", "policy": runner.POLICY,
                "source_review": {"basis": "independent synthetic fixture"}, "source_scope": "fixture",
                "entries": [{"prefix": "test_tiny", "core": ["test_one", "test_two"], "gui": []}]}
    write(root / "registry.json", registry)
    identities = ["test_tiny.test_one", "test_tiny.test_two"]
    inventory = {"schema": "zhuyin-test-groups/2", "policy": runner.POLICY,
        "window_validation": runner.audit.WINDOW_NOTICE, "core_ids": identities, "pytest_ids": identities,
        "cancelled_window_ids": [], "records": [{"identity": identity, "nodeid": "tests/test_tiny.py::" + identity.split(".")[-1], "group": "core"} for identity in identities],
        "registration": {"registry_sha256": evidence.sha256_file(root / "registry.json"),
                         "source_review": registry["source_review"], "source_scope": "fixture"}}
    write(root / "inventory.json", inventory)
    (root / "junit.xml").write_text('<testsuites><testsuite><testcase classname="tests.test_tiny" name="test_one"/><testcase classname="tests.test_tiny" name="test_two"/></testsuite></testsuites>', encoding="utf-8")
    lock = ROOT / "requirements-ci-lock.txt"
    (root / "lock.txt").write_bytes(lock.read_bytes())
    dependencies = {re.sub(r"[-_.]+", "-", line.split("==")[0]).lower(): line.split("==")[1]
                    for line in lock.read_text().splitlines() if line and not line.startswith("#")}
    environment = {"python": "3.13.0", "platform": "Windows", "capture": "fd", "dependencies": dependencies,
                   "environment": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTEST_ADDOPTS": "", "TCL_LIBRARY": "", "TK_LIBRARY": ""}}
    candidate = {"head": SYNTHETIC if event == "pull_request" else MERGE, "tree": TREE, "parents": [BASE, HEAD]}
    ci = {"run_id": 100, "attempt": 1, "job": "test", "event": event}
    checkout = {"number": 20, "repository": evidence.REPOSITORY, "base_repository": evidence.REPOSITORY,
                "head_repository": evidence.REPOSITORY, "base_ref": base, "head_ref": "codex/fixture", "base_sha": BASE, "head_sha": HEAD}
    if event == "push": checkout = {"repository": evidence.REPOSITORY, "ref": "refs/heads/main", "before": BASE}
    write(root / "environment.json", {"schema": "zhuyin-current-validation-environment/1", "policy": evidence.CURRENT_POLICY,
           "window_validation": evidence.WINDOW_NOTICE, "candidate": candidate, "ci": ci, "checkout": checkout, "environment": environment})
    write(root / "event.json", {"ci": ci, "checkout": checkout})
    write(root / "assets.json", {"fixture": "immutable synthetic manifest, not product integrity PASS"})
    checks = []
    commands = evidence.current_commands(event)
    commands["diff"] += [BASE + "..." + HEAD if event == "pull_request" else BASE + ".." + MERGE]
    for name, expected in commands.items():
        command = ["python", *expected[1:]] if expected[0] == "<python>" else expected
        log = root / (name + ".log")
        log.write_text(json.dumps({"argv": command}) + "\n" + ("synthetic command output\n" if name != "clean" else "") + "\nexit_code=0\n", encoding="utf-8")
        checks.append({"name": name, "command": command, "exit_code": 0, "log": reference(root, log)})
    result = {"schema": evidence.CURRENT_COVERAGE_SCHEMA, "policy": evidence.CURRENT_POLICY,
              "window_validation": evidence.WINDOW_NOTICE, "repository": evidence.REPOSITORY, "status": "success",
              "candidate": candidate, "ci": ci, "checkout": checkout, "environment": environment,
              "checks": checks, "artifacts": {key: reference(root, root / filename) for key, filename in {
                  "inventory": "inventory.json", "junit": "junit.xml", "lock": "lock.txt", "registry": "registry.json",
                  "environment": "environment.json", "event": "event.json", "assets": "assets.json"}.items()}}
    path = root / "current-coverage.json";write(path, result)
    return path


def current_short(root):
    """Retain existing independent short-command fixture, replace only PR inputs."""
    coverage, short_path = short_fixture(root)
    old_root = coverage.parent
    shutil.rmtree(old_root)
    coverage = bundle(old_root)
    current = evidence.load_json(coverage)
    (old_root / 'main-full').mkdir()
    (old_root / 'lock.txt').rename(old_root / 'main-full/lock.txt')
    current['artifacts']['lock'] = reference(old_root, old_root / 'main-full/lock.txt')
    write(coverage, current)
    data = evidence.load_json(short_path)
    data.update(schema=evidence.CURRENT_SHORT_SCHEMA, policy=evidence.CURRENT_POLICY,
                window_validation=evidence.WINDOW_NOTICE, environment=current["environment"],
                coverage_sha256=evidence.sha256_file(coverage), coverage_path="pr-evidence/current-coverage.json")
    for key in ("pr", "artifact"):
        path = root / (key + ".json");value = evidence.load_json(path)
        if key == "pr": value["base"]["sha"] = BASE;value["head"]["ref"] = "codex/fixture"
        else:value["name"] = "current-validation-evidence-100-1"
        write(path, value);data["source"][key] = reference(root, path)
    with zipfile.ZipFile(root / "pr-artifact.zip", "w") as archive:
        for file in old_root.rglob("*"):
            if file.is_file(): archive.write(file, file.relative_to(old_root).as_posix())
    data["source"]["archive"] = reference(root, root / "pr-artifact.zip")
    artifact = evidence.load_json(root / "artifact.json");artifact["digest"] = "sha256:" + evidence.sha256_file(root / "pr-artifact.zip")
    write(root / "artifact.json", artifact);data["source"]["artifact"] = reference(root, root / "artifact.json")
    for key in ("assets", "pr_assets"):
        (root / (key + ".json")).write_bytes((old_root / "assets.json").read_bytes())
        data["source"][key] = reference(root, root / (key + ".json"))
        data[key + "_sha256"] = evidence.sha256_file(root / (key + ".json"))
    write(short_path, data)
    return coverage, short_path


class CurrentEvidenceTests(unittest.TestCase):
    def test_explicit_current_readers_reject_old_schema_without_history_dispatch(self):
        from test_validation_evidence import NoWindowEvidenceTests
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);legacy=root/'legacy';legacy.mkdir()
            old=NoWindowEvidenceTests().new_bundle(legacy)
            current=bundle(root/'current')
            state=staged_state(old);state['validation_policy']=evidence.CURRENT_POLICY
            state['task']['head_branch']=state['pr']['head_branch']=state['pr_ci']['branch']='codex/fixture';state['pr']['number']=20
            names=('Check out repository','Set up Python','Show Python version','Upgrade pip','Install development dependencies',
                'Validate runtime asset integrity','Audit test entrypoint coverage','Run full pytest suite','Verify active test execution',
                'Compile Python sources','Check committed whitespace (pull request)','Check repository working tree','Capture main full environment',
                'Produce current validation coverage','Verify current validation coverage','Upload main full evidence','Verify required validation results')
            state['pr_ci']['jobs']=[dict(state['pr_ci']['jobs'][0],steps={name:'success' for name in names})]
            formalize(state,old)
            good_short_root=root/'short';good_short_root.mkdir();_,short=current_short(good_short_root)
            data=evidence.load_json(current);repo={'full_name':evidence.REPOSITORY}
            responses={'pr':{'number':20,'merged':True,'state':'closed','merge_commit_sha':MERGE,
                'base':{'ref':'develop','sha':BASE,'repo':repo},'head':{'ref':'codex/fixture','sha':HEAD,'repo':repo}},
                'commit':{'sha':MERGE,'parents':[{'sha':BASE},{'sha':HEAD}],'commit':{'tree':{'sha':TREE}}},
                'run':{'id':100,'run_attempt':1,'run_number':50,'event':'pull_request','head_sha':HEAD,
                    'path':'.github/workflows/ci.yml','status':'completed','conclusion':'success'}}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as archive:
                for file in legacy.rglob('*'):
                    if file.is_file():archive.write(file,'current-coverage.json' if file==old else file.relative_to(legacy).as_posix())
            payload=raw.getvalue();artifact={'id':7,'name':'current-validation-evidence-100-1','expired':False,
                'digest':'sha256:'+hashlib.sha256(payload).hexdigest()}
            class Transport:
                def request(self,endpoint,*,binary=False):
                    if endpoint.startswith('commits/'):return responses['commit']
                    if endpoint.startswith('pulls/'):return responses['pr']
                    if endpoint=='actions/runs/100':return responses['run']
                    if endpoint=='actions/artifacts/7/zip':return payload
                    raise AssertionError(endpoint)
                def pages(self,endpoint,key=None):
                    if endpoint.endswith('/pulls'):return [responses['pr']]
                    if 'head_sha='+HEAD in endpoint:return [responses['run']]
                    if endpoint=='actions/runs/100/artifacts':return [artifact]
                    raise AssertionError(endpoint)
            with patch.object(runner,'aggregate',side_effect=AssertionError('current entrypoint must never aggregate old history')) as aggregate,patch.object(runner,'read_verified_manifest',side_effect=AssertionError('current entrypoint must never read old manifests')) as read:
                self.assertEqual(evidence.verify_current_coverage(current)['status'],'success')
                with self.assertRaises(ValueError):evidence.verify_current_coverage(old)
                with self.assertRaises(ValueError):evidence.verify_reuse(old,short,current_only=True)
                result=gate.next_action(state);self.assertEqual(result['action'],'REFRESH_EVIDENCE');self.assertIn('functional evidence invalid',result['reason'])
                output=root/'download';output.mkdir()
                with self.assertRaises(ValueError):evidence.fetch_pr_evidence(Transport(),{'head':MERGE,'tree':TREE,'parents':[BASE,HEAD]},output,current_only=True)
                aggregate.assert_not_called();read.assert_not_called()

    def test_current_producer_retains_committed_source_and_checks_real_raw_exit(self):
        # Tiny synthetic Git merge and invented two-case results; never product/full CI PASS.
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir();raw=source/'tmp';path=bundle(raw)
            value=evidence.load_json(path);(source/'scripts').mkdir()
            for origin,destination in [('lock.txt','requirements-ci-lock.txt'),('registry.json','scripts/test_group_registry.json'),('assets.json','runtime_asset_manifest.json')]:
                (source/destination).write_bytes((raw/origin).read_bytes())
            def git(*args,input=None):
                return subprocess.check_output(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],cwd=source,input=input).decode().strip()
            git('init','-q');git('config','core.autocrlf','false');git('add','requirements-ci-lock.txt','scripts/test_group_registry.json','runtime_asset_manifest.json')
            tree=git('write-tree');base=git('commit-tree',tree,input=b'base\n');head=git('commit-tree',tree,'-p',base,input=b'feature\n')
            merged=git('commit-tree',tree,'-p',base,'-p',head,input=b'synthetic PR merge\n');git('update-ref','HEAD',merged)
            (source/'.git/info/exclude').write_text('tmp/\n',encoding='utf-8')
            candidate={'head':merged,'tree':tree,'parents':[base,head]}
            value['candidate']=candidate;value['checkout'].update(base_sha=base,head_sha=head)
            capture=evidence.load_json(raw/'environment.json');capture.update(candidate=candidate,checkout=value['checkout'])
            (raw/'main-full').mkdir();write(raw/'main-full/environment.json',capture)
            (raw/'ci-test-inventory.json').write_bytes((raw/'inventory.json').read_bytes());(raw/'ci-pytest.xml').write_bytes((raw/'junit.xml').read_bytes())
            for item in value['checks']:
                command=item['command']
                if item['name']=='diff':command[-1]=base+'...'+head
                text=json.dumps({'argv':command})+'\n'+('fixture output\n' if item['name']!='clean' else '')+'\nexit_code=0\n'
                (raw/'main-full'/(item['name']+'.log')).write_text(text,encoding='utf-8')
            with patch.object(evidence,'ROOT',source),patch.object(runner,'ROOT',source),patch.object(runner,'environment',return_value=value['environment']):
                output=raw/'produced.json'
                self.assertEqual(evidence.produce_current_coverage(raw,output)['candidate'],candidate)
                self.assertEqual(evidence.verify_coverage(output)['status'],'success')
                log=raw/'main-full/pytest.log';log.write_text(log.read_text().replace('exit_code=0','exit_code=1'))
                (raw/'main-full/event.json').unlink()
                with self.assertRaisesRegex(ValueError,'raw current command/exit'):evidence.produce_current_coverage(raw,raw/'failed.json')
                self.assertFalse((raw/'failed.json').exists(), 'failed raw execution cannot publish successful current coverage')

    def test_current_routes_verify_without_any_history_artifact_and_relocate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for event, base in (("pull_request", "develop"), ("pull_request", "main"), ("push", "main")):
                path = bundle(root / (event + base), event=event, base=base)
                self.assertEqual(evidence.verify_coverage(path)["status"], "success")
                self.assertFalse(any(path.parent.glob("*history*")))
            moved = root / "moved";shutil.copytree(root / "pull_requestdevelop", moved);shutil.rmtree(root / "pull_requestdevelop")
            self.assertEqual(evidence.verify_coverage(moved / "current-coverage.json")["status"], "success")
            (moved / "pytest.log").unlink()
            with self.assertRaises((ValueError, OSError)):evidence.verify_coverage(moved / "current-coverage.json")

    def test_current_checksum_valid_identity_inventory_environment_and_exit_fail_closed(self):
        mutations = [lambda b: b.update(policy=runner.POLICY), lambda b:b.update(schema="zhuyin-validation-coverage/99"),
            lambda b:b["candidate"].update(head=HEAD), lambda b:b["candidate"].update(parents=[HEAD,BASE]),
            lambda b:b["ci"].update(job="grouped"), lambda b:b["ci"].update(attempt=0),
            lambda b:b["checkout"].update(base_sha="f"*40), lambda b:b["checkout"].update(base_ref="other"),
            lambda b:b.update(status="failed"),lambda b:b["checks"][4].update(exit_code=1),
            lambda b:b["checks"][4]["command"].append("--capture=sys"), lambda b:b["checks"].pop()]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, mutation in enumerate(mutations):
                path = bundle(root / str(index));value = evidence.load_json(path);mutation(value);write(path,value)
                with self.subTest(index=index), self.assertRaises((ValueError,KeyError)): evidence.verify_coverage(path)
            for name in ("failure", "skipped", "error", "missing", "duplicate"):
                path = bundle(root / name);value=evidence.load_json(path);junit=path.parent/"junit.xml"
                raw=junit.read_text()
                if name in ("failure","skipped","error"):raw=raw.replace('name="test_one"/>','name="test_one"><'+name+'/></testcase>')
                elif name=="missing":raw=raw.replace('<testcase classname="tests.test_tiny" name="test_two"/>','')
                else:raw=raw.replace('name="test_two"','name="test_one"')
                junit.write_text(raw);value["artifacts"]["junit"]=reference(path.parent,junit);write(path,value)
                with self.subTest(junit=name),self.assertRaises(ValueError):evidence.verify_coverage(path)
            for kind in ("raw_exit", "dependencies", "inventory"):
                path=bundle(root/kind);value=evidence.load_json(path)
                if kind=="raw_exit":
                    log=path.parent/"pytest.log";log.write_text(log.read_text().replace('exit_code=0','exit_code=1'))
                    value["checks"][4]["log"]=reference(path.parent,log)
                elif kind=="dependencies":
                    value["environment"]["dependencies"]["pip"]="99";env=path.parent/"environment.json"
                    original=evidence.load_json(env);original["environment"]=value["environment"];write(env,original);value["artifacts"]["environment"]=reference(path.parent,env)
                else:
                    inv=path.parent/"inventory.json";original=evidence.load_json(inv);original["core_ids"].pop();original["pytest_ids"]=original["core_ids"][:];original["records"].pop();write(inv,original);value["artifacts"]["inventory"]=reference(path.parent,inv)
                write(path,value)
                with self.subTest(kind=kind),self.assertRaises(ValueError):evidence.verify_coverage(path)

    def test_current_reuse_actual_merge_only_and_metadata_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);coverage,short=current_short(root)
            self.assertEqual(evidence.verify_reuse(coverage,short)["outcome"],"success")
            mutations=[("pr", lambda p:p.update(number=21)), ("pr",lambda p:p["base"].update(sha="f"*40)),
                ("pr",lambda p:p["base"].update(ref="main")),("commit",lambda p:p.update(parents=[{"sha":HEAD},{"sha":BASE}])),
                ("commit",lambda p:p["commit"]["tree"].update(sha="f"*40)),("run",lambda p:p.update(run_attempt=2)),
                ("artifact",lambda p:p.update(expired=True)),("artifact",lambda p:p.update(name="validation-evidence-100-1")),
                ("runs",lambda p:p.append({**p[0],"id":101,"run_number":51,"conclusion":"failure"}))]
            original=evidence.load_json(short)
            for key,mutation in mutations:
                file=root/(key+".json");before=file.read_bytes();data=evidence.load_json(file);mutation(data);write(file,data)
                changed=deepcopy(original);changed["source"][key]=reference(root,file);write(short,changed)
                with self.subTest(key=key),self.assertRaises(ValueError):evidence.verify_reuse(coverage,short)
                file.write_bytes(before);write(short,original)
            moved=root.parent/(root.name+'-moved');shutil.copytree(root,moved)
            try:self.assertEqual(evidence.verify_reuse(moved/'pr-evidence/current-coverage.json',moved/'short.json')["outcome"],"success")
            finally:shutil.rmtree(moved)

    def test_current_gate_dispatch_keeps_review_protection_and_old_policy_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            path=bundle(Path(folder));state=staged_state(path);state["validation_policy"]=evidence.CURRENT_POLICY
            state["pr"]["number"]=20;state["pr"]["head_branch"]="codex/fixture";state["task"]["head_branch"]="codex/fixture";state["pr_ci"]["branch"]="codex/fixture"
            names=("Check out repository", "Set up Python", "Show Python version", "Upgrade pip", "Install development dependencies",
                "Validate runtime asset integrity", "Audit test entrypoint coverage", "Run full pytest suite", "Verify active test execution",
                "Compile Python sources", "Check committed whitespace (pull request)", "Check repository working tree", "Capture main full environment",
                "Produce current validation coverage", "Verify current validation coverage", "Upload main full evidence", "Verify required validation results")
            state["pr_ci"]["jobs"]=[dict(state["pr_ci"]["jobs"][0],steps={name:"success" for name in names})]
            formalize(state,path)
            self.assertEqual(gate.next_action(state)["action"],"MERGE_PROPOSAL")
            for name in names:
                for outcome in ("failure","skipped","cancelled",""):
                    changed=deepcopy(state);changed["pr_ci"]["jobs"][0]["steps"][name]=outcome
                    with self.subTest(name=name,outcome=outcome):self.assertNotEqual(gate.next_action(changed)["action"],"MERGE_PROPOSAL")
            for mutation in (lambda s:s["pr"].update(protection_satisfied=False), lambda s:s.update(validation_policy=runner.POLICY),
                             lambda s:s["reviews"].pop(),lambda s:s["pr_ci"].update(tested_sha="f"*40)):
                changed=deepcopy(state);mutation(changed);self.assertNotEqual(gate.next_action(changed)["action"],"MERGE_PROPOSAL")

    def test_current_short_upload_roundtrip_keeps_exact_downloaded_artifact_closure(self):
        from tests.test_ci_validation_workflow import current_job, step
        import re
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=root/'producer';scope=workspace/'tmp/validation-evidence/short-synthetic';scope.mkdir(parents=True)
            coverage,short=current_short(scope)
            # Installation precheck attachments are retained alongside, never
            # substituted for the existing exact current-PR/merge-short closure.
            precheck = workspace/'tmp/validation-evidence/locked-environment.json'
            precheck_log = precheck.with_suffix('.log')
            write(precheck, {'schema': 'zhuyin-locked-environment-check/1', 'status': 'success', 'environment': evidence.load_json(short)['environment']})
            precheck_log.write_text(json.dumps({'argv': ['fixture-python', 'scripts/release_ci_evidence.py', 'verify-environment', '--output', str(precheck)]})+'\nexit_code=0\n')
            precheck_bytes, precheck_log_bytes = precheck.read_bytes(), precheck_log.read_bytes()
            (scope/'personal.txt').write_text('synthetic excluded canary; not user data',encoding='utf-8')
            upload=step(current_job('short'),'Upload validation evidence')
            block=re.search(r'^          path: \|\n((?:            .*\n)+)',upload,re.M).group(1)
            patterns=[line.strip() for line in block.splitlines()]
            included=set()
            for pattern in patterns:
                if not pattern.startswith('!'):included.update(path for path in workspace.glob(pattern) if path.is_file())
            for pattern in patterns:
                if pattern.startswith('!'):included.difference_update(workspace.glob(pattern[1:]))
            lock=scope/'pr-evidence/main-full/lock.txt'
            self.assertIn(lock,included);self.assertNotIn(scope/'personal.txt',included)
            self.assertIn(precheck, included);self.assertIn(precheck_log, included)
            archive=root/'uploaded.zip'
            with zipfile.ZipFile(archive,'w') as output:
                for path in sorted(included):output.write(path,path.relative_to(workspace/'tmp/validation-evidence').as_posix())
            missing=io.BytesIO()
            with zipfile.ZipFile(archive) as retained,zipfile.ZipFile(missing,'w') as damaged:
                for item in retained.infolist():
                    if item.filename!='short-synthetic/pr-evidence/main-full/lock.txt':damaged.writestr(item,retained.read(item))
            extracted=root/'relocated';evidence.extract_evidence(archive.read_bytes(),extracted)
            shutil.rmtree(workspace)
            relocated=extracted/'short-synthetic'
            self.assertEqual((extracted/'locked-environment.json').read_bytes(), precheck_bytes)
            self.assertEqual((extracted/'locked-environment.log').read_bytes(), precheck_log_bytes)
            self.assertEqual(evidence.verify_reuse(relocated/'pr-evidence/current-coverage.json',relocated/'short.json')['outcome'],'success')
            missing_root=root/'missing';evidence.extract_evidence(missing.getvalue(),missing_root)
            with self.assertRaises((OSError,ValueError)):
                evidence.verify_reuse(missing_root/'short-synthetic/pr-evidence/current-coverage.json',missing_root/'short-synthetic/short.json')

    def test_current_fetch_only_exact_latest_pr_artifact_with_real_archive_verifier(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);coverage,short=current_short(root);original=evidence.load_json(short)
            responses={key:evidence.load_json(root/(key+'.json')) for key in ('pr','run','commit','artifact','runs')}
            archive=(root/'pr-artifact.zip').read_bytes()
            class Transport:
                def __init__(self):self.calls=[]
                def request(self,endpoint,*,binary=False):
                    self.calls.append(endpoint)
                    if endpoint.startswith('commits/'):return responses['commit']
                    if endpoint.startswith('pulls/'):return responses['pr']
                    if endpoint=='actions/runs/100':return responses['run']
                    if endpoint=='actions/artifacts/7/zip':return archive
                    raise AssertionError('unexpected historical request: '+endpoint)
                def pages(self,endpoint,key=None):
                    self.calls.append(endpoint)
                    if endpoint.endswith('/pulls'):return [{**responses['pr'],'state':'closed'}]
                    if 'head_sha='+HEAD in endpoint:return responses['runs']
                    if endpoint=='actions/runs/100/artifacts':return [responses['artifact']]
                    raise AssertionError('unexpected historical pagination: '+endpoint)
            transport=Transport();out=root/'download';out.mkdir()
            candidate=evidence.load_json(short)['candidate']
            downloaded,result,source=evidence.fetch_pr_evidence(transport,candidate,out,current_only=True)
            self.assertEqual(evidence.verify_coverage(downloaded),result)
            self.assertEqual(sum(call.endswith('/zip') for call in transport.calls),1)
            self.assertFalse(any('attempts/' in call or 'branch=' in call for call in transport.calls))
            for key,mutation in [('pr',lambda p:p.update(number=21)),('artifact',lambda p:p.update(expired=True)),('run',lambda p:p.update(conclusion='failure'))]:
                saved=deepcopy(responses[key]);mutation(responses[key]);negative=root/('negative-'+key);negative.mkdir()
                with self.subTest(key=key),self.assertRaises(ValueError):evidence.fetch_pr_evidence(Transport(),candidate,negative,current_only=True)
                responses[key]=saved
