"""Bounded workflow regressions; execute the real required-check PowerShell body.

No Actions run, GUI, Git write, credential request or functional suite is started.
"""
from pathlib import Path
import os
import json
import tempfile
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')


def job(name):
    match = re.search(r'^  ' + re.escape(name) + r':\n(.*?)(?=^  [a-z][a-z_]*:|\Z)', WORKFLOW, re.M | re.S)
    if match is None:
        raise AssertionError('required job missing: ' + name)
    return match.group(1)


def step(section, name):
    match = re.search(r'^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - name:|\Z)', section, re.M | re.S)
    if match is None:
        raise AssertionError('required step missing: ' + name)
    return match.group(1)


def expression(text, context):
    """Evaluate only the grammar used by the actual workflow expressions."""
    text = text.strip().removeprefix('${{').removesuffix('}}').strip()
    tokens = re.findall(r"'[^']*'|&&|\|\||==|!=|[!(),]|[A-Za-z_][A-Za-z0-9_.-]*|[0-9]+", text)
    if re.sub(r'\s+', '', ''.join(tokens)) != re.sub(r"\s+", '', re.sub(r"'[^']*'", lambda m: m.group().replace(' ', ''), text)):
        raise AssertionError('unsupported workflow expression: ' + text)
    index = 0

    def atom():
        nonlocal index
        token = tokens[index]; index += 1
        if token == '!': return not bool(atom())
        if token == '(':
            value = either()
            if tokens[index] != ')': raise AssertionError('unbalanced expression')
            index += 1; return value
        if token.startswith("'"): return token[1:-1]
        if token.isdecimal(): return int(token)
        if token in ('true', 'false'): return token == 'true'
        if index < len(tokens) and tokens[index] == '(':
            index += 1
            if token == 'fromJSON': value = json.loads(either())
            else: value = {'always': True, 'success': context.get('_success', True),
                           'cancelled': context.get('_cancelled', False),
                           'failure': not context.get('_success', True)}[token]
            if tokens[index] != ')': raise AssertionError('unsupported function arguments')
            index += 1; return value
        value = context
        for component in token.split('.'):
            value = value.get(component, '') if isinstance(value, dict) else ''
        return value

    def compare():
        nonlocal index
        left = atom()
        if index < len(tokens) and tokens[index] in ('==', '!='):
            operator = tokens[index]; index += 1; right = atom()
            equal = left.casefold() == right.casefold() if isinstance(left, str) and isinstance(right, str) else left == right
            return equal if operator == '==' else not equal
        return left

    def both():
        nonlocal index
        left = compare()
        while index < len(tokens) and tokens[index] == '&&':
            index += 1; right = compare(); left = right if left else left
        return left

    def either():
        nonlocal index
        left = both()
        while index < len(tokens) and tokens[index] == '||':
            index += 1; right = both(); left = left if left else right
        return left

    value = either()
    if index != len(tokens): raise AssertionError('unconsumed workflow expression')
    return value


def condition(section, context):
    found = re.search(r'^ +if: (.*)$', section, re.M)
    value = found.group(1) if found else 'success()'
    implicit = not re.search(r'\b(always|cancelled|failure|success)\(', value)
    return bool(expression(value, context)) and (not implicit or context.get('_success', True))


def steps(section):
    return [(m.group(1), m.group(2)) for m in re.finditer(
        r'^      - name: ([^\n]+)\n(.*?)(?=^      - name:|\Z)', section, re.M | re.S)]


def action(section):
    uses = re.search(r'^        uses: (.*)$', section, re.M)
    if uses: return ('uses', uses.group(1))
    run = re.search(r'^        run: (.*)$', section, re.M)
    if not run: raise AssertionError('selected step has no real run/uses')
    if run.group(1) in ('|', '>-'):
        lines = [line[10:] for line in section[run.end():].splitlines() if line.startswith('          ')]
        return ('run', (' ' if run.group(1) == '>-' else '\n').join(lines))
    return ('run', run.group(1))


def event_context(event='pull_request', ref='refs/pull/41/merge', **changes):
    github = dict(event_name=event, ref=ref, head_ref='chore/validation-flow-reduction',
                  repository='discoveryray/zhuyin-proofreader',
                  event=dict(before='1' * 40, pull_request=dict(number=41,
                    head=dict(repo=dict(full_name='discoveryray/zhuyin-proofreader')),
                    base=dict(ref='develop', sha='2' * 40))))
    for path, value in changes.items():
        target = github; components = path.split('__')
        for component in components[:-1]: target = target[component]
        target[components[-1]] = value
    return dict(github=github, needs=dict(grouped=dict(result='skipped'), short=dict(result='skipped')),
                _success=True, _cancelled=False)


def planned(context):
    return {name: [(label, action(body)) for label, body in steps(job(name)) if condition(body, context)]
            for name in ('grouped', 'short', 'test') if condition(job(name).split('    steps:', 1)[0], context)}


def summary_environment(context, outcomes):
    context = {**context, 'steps': {key: dict(outcome=value) for key, value in outcomes.items()}}
    body = step(job('test'), 'Verify required validation results')
    bindings = body.split('        env:\n', 1)[1].split('        run:', 1)[0]
    environment = {}
    for key, value in re.findall(r'^          ([A-Z_]+): (.*)$', bindings, re.M):
        actual = expression(value, context)
        environment[key] = str(actual).lower() if isinstance(actual, bool) else str(actual)
    return environment


class ValidationWorkflowTests(unittest.TestCase):
    def test_required_check_executes_real_failure_sensitive_summary(self):
        pr = event_context(); pr['needs']['grouped']['result'] = 'success'
        develop = event_context('push', 'refs/heads/develop'); develop['needs']['short']['result'] = 'success'
        main = event_context('push', 'refs/heads/main')
        legacy = event_context(event__pull_request__number=29, head_ref='codex/review-confirm-responsive',
                               event__pull_request__base__sha='502414b3b38e004a6d8d9cb693cf21b65148765a')
        ids = ['checkout', 'python', 'version', 'pip', 'dependencies', 'runtime', 'inventory', 'pytest', 'active', 'compile', 'clean', 'diff_push']
        success = {key: 'success' for key in ids}; success.update(unittest='skipped', diff_pr='skipped')
        cases = [(pr, {}, True), (develop, {}, True), (main, success, True),
                 (event_context('workflow_dispatch', 'refs/heads/main'), success, False),
                 (event_context('push', 'refs/heads/other'), success, False)]
        for result in ('failure', 'cancelled', 'skipped', ''):
            for key in ids: cases.append((main, {**success, key: result}, False))
            for context, required in ((pr, 'grouped'), (develop, 'short')):
                changed = {**context, 'needs': {k: dict(v) for k, v in context['needs'].items()}}
                changed['needs'][required]['result'] = result; cases.append((changed, {}, False))
        legacy_success = {**success, 'diff_push': 'skipped', 'diff_pr': 'success', 'unittest': 'success'}
        cases.append((legacy, legacy_success, True))
        for result in ('failure', 'cancelled', 'skipped', ''):
            for key in [name for name in ids if name != 'diff_push'] + ['unittest', 'diff_pr']:
                cases.append((legacy, {**legacy_success, key: result}, False))
        for context in (pr, develop, main, legacy):
            changed = {**context, 'needs': dict(grouped=dict(result='success'), short=dict(result='success'))}
            cases.append((changed, legacy_success if context is legacy else success, False))
        self.execute_summary(cases)
        self.assertIn('needs: [grouped, short]', job('test'))
        self.assertTrue(condition(job('test').split('    steps:', 1)[0], {**main, '_success': False}))
        self.assertTrue(condition(step(job('test'), 'Verify required validation results'), {**main, '_success': False}))

    def execute_summary(self, cases):
        kind, script = action(step(job('test'), 'Verify required validation results'))
        self.assertEqual(kind, 'run')
        shell = shutil.which('pwsh') or shutil.which('powershell')
        self.assertIsNotNone(shell, 'Windows CI requires PowerShell')
        inputs = [dict(env=summary_environment(context, outcomes), accepted=accepted)
                  for context, outcomes, accepted in cases]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cases.json'; path.write_text(json.dumps(inputs), encoding='utf-8')
            driver = """$cases = Get-Content -Raw -LiteralPath $env:SUMMARY_CASES | ConvertFrom-Json
$body = [scriptblock]::Create($env:SUMMARY_BODY)
foreach ($case in $cases) {
  foreach ($property in $case.env.PSObject.Properties) { [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process') }
  try { & $body; $accepted = $true; $errorText = '' } catch { $accepted = $false; $errorText = $_.Exception.Message }
  @{ accepted=$accepted; expected=$case.accepted; env=$case.env; error=$errorText } | ConvertTo-Json -Compress
  if ($accepted -ne $case.accepted) { exit 1 }
}
"""
            result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', driver],
                                    env={**os.environ, 'SUMMARY_CASES': str(path), 'SUMMARY_BODY': script},
                                    capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode('utf-8', errors='replace'))
        records = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines()]
        self.assertEqual(len(records), len(cases))
        self.assertTrue(all(record['accepted'] == record['expected'] for record in records))
        self.assertTrue(all(record['accepted'] or record['error'] for record in records))
        print(result.stdout.decode('utf-8'))  # Preserve actual PowerShell decisions in passing JUnit.

    def test_actual_job_and_step_conditions_route_event_branch_matrix(self):
        pr = event_context(); develop = event_context('push', 'refs/heads/develop'); main = event_context('push', 'refs/heads/main')
        self.assertEqual(set(planned(pr)), {'grouped', 'test'})
        self.assertEqual(set(planned(develop)), {'short', 'test'})
        self.assertEqual(set(planned(main)), {'test'})
        self.assertEqual(set(planned(event_context('workflow_dispatch', 'refs/heads/main'))), {'test'})
        for context in (pr, develop):
            self.assertEqual(set(dict(planned(context)['test'])), {'Check out repository', 'Set up Python', 'Show Python version', 'Verify required validation results'})
        selected = dict(planned(main)['test'])
        required = {'Check out repository', 'Set up Python', 'Show Python version', 'Upgrade pip', 'Install development dependencies',
                    'Validate runtime asset integrity', 'Audit test entrypoint coverage', 'Run full pytest suite', 'Verify active test execution',
                    'Compile Python sources', 'Check committed whitespace (push)', 'Check repository working tree', 'Verify required validation results'}
        self.assertEqual(set(selected), required)
        self.assertEqual(selected['Run full pytest suite'], ('run', 'python -m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml'))
        self.assertEqual(selected['Verify active test execution'], ('run', 'python scripts/test_entrypoint_audit.py verify-active tmp/ci-test-inventory.json --junit tmp/ci-pytest.xml'))
        self.assertEqual(selected['Validate runtime asset integrity'], ('run', 'python -m pytest tests/test_runtime_asset_manifest_integrity_v562.py -q'))
        self.assertEqual(selected['Check out repository'], ('uses', 'actions/checkout@v7'))
        self.assertEqual(selected['Set up Python'], ('uses', 'actions/setup-python@v7'))
        matrix = re.search(r'^        python-version: (.*)$', job('test'), re.M).group(1)
        self.assertEqual(expression(matrix, main), ['3.13'])
        version = re.search(r'^          python-version: (.*)$', step(job('test'), 'Set up Python'), re.M).group(1)
        self.assertEqual(expression(version, {**main, 'matrix': {'python-version': '3.13'}}), '3.13.0')
        self.assertFalse(condition(step(job('test'), 'Run full unittest suite'), main))
        self.assertFalse(condition(step(job('test'), 'Run full pytest suite'), {**main, '_cancelled': True}))

    def test_exact_legacy_conditions_select_original_validation_steps(self):
        contexts = [event_context(event__pull_request__number=29, head_ref='codex/review-confirm-responsive', event__pull_request__base__sha='502414b3b38e004a6d8d9cb693cf21b65148765a'),
                    event_context(head_ref='codex/simplify-validation', event__pull_request__base__sha='1593e7af65596d320b4427f1b15bb2bc0bdc949c'),
                    event_context('push', 'refs/heads/develop', event__before='502414b3b38e004a6d8d9cb693cf21b65148765a')]
        for context in contexts:
            with self.subTest(event=context['github']):
                self.assertEqual(set(planned(context)), {'test'})
                selected = dict(planned(context)['test'])
                self.assertEqual(selected['Run full unittest suite'], ('run', 'python -m unittest discover -s tests -p "test_*.py"'))
                self.assertTrue('Run full pytest suite' in selected and 'Verify active test execution' in selected)
                matrix = re.search(r'^        python-version: (.*)$', job('test'), re.M).group(1)
                self.assertEqual(expression(matrix, context), ['3.12', '3.13'])
                outcomes = {re.search(r'^        id: (.*)$', body, re.M).group(1): 'success'
                            for label, body in steps(job('test')) if condition(body, context) and re.search(r'^        id: ', body, re.M)}
                self.execute_summary([(context, outcomes, True)])
        base = dict(head_ref='codex/review-confirm-responsive', event__pull_request__number=29,
                    event__pull_request__base__sha='502414b3b38e004a6d8d9cb693cf21b65148765a')
        for changes in ({'event__pull_request__number': 30}, {'event__pull_request__head__repo__full_name': 'other/repo'},
                        {'event__pull_request__base__ref': 'main'}, {'event__pull_request__base__sha': '3' * 40}):
            self.assertEqual(set(planned(event_context(**(base | changes)))), {'grouped', 'test'})

    def test_new_paths_keep_default_capture_and_independent_group_processes(self):
        grouped = job('grouped')
        for section in (grouped, job('short')):
            self.assertEqual(re.findall(r'^      PYTEST_ADDOPTS: (.*)$', section, re.M), ['""'])
            self.assertNotIn('--capture=sys', section)
        self.assertIn('python-version: \'3.13.0\'', grouped)
        body = step(grouped, 'Run core group')
        self.assertIn('--group core --mode validation', body)
        self.assertNotIn('continue-on-error', body)
        self.assertNotIn('!cancelled()', body)
        self.assertLess(grouped.index('Restore validation history'), grouped.index('Run core group'))
        self.assertLess(grouped.index('Run core group'), grouped.index('Aggregate functional coverage'))
        self.assertLess(grouped.index('Aggregate functional coverage'), grouped.index('Verify functional coverage'))
        self.assertIn('verify-coverage tmp/validation-evidence/coverage.json', step(grouped, 'Verify functional coverage'))
        self.assertNotIn('python -m pytest tests/', grouped)
        self.assertNotIn('unittest discover', grouped)

    def test_early_preflight_precedes_core_and_shared_wiring_does_not_open_post_merge_tk(self):
        # All event/ref paths use this one workflow; reject retired launchers globally.
        for forbidden in ('validation_tk_environment.py', 'gui_preflight.py', '--group gui',
                          'verify-gui', 'steps.gui.outcome', 'steps.tk_environment.outcome',
                          'GUI_RESULT', 'TK_ENVIRONMENT_RESULT'):
            self.assertNotIn(forbidden, WORKFLOW)
        self.assertIn('Verify active test execution', job('test'))
        for context in (event_context(), event_context('push', 'refs/heads/develop'),
                        event_context('push', 'refs/heads/main'),
                        event_context('workflow_dispatch', 'refs/heads/main'),
                        event_context(event__pull_request__number=29, head_ref='codex/review-confirm-responsive',
                                      event__pull_request__base__sha='502414b3b38e004a6d8d9cb693cf21b65148765a'),
                        event_context(head_ref='codex/simplify-validation',
                                      event__pull_request__base__sha='1593e7af65596d320b4427f1b15bb2bc0bdc949c')):
            for selected in planned(context).values():
                for label, (_, command) in selected:
                    self.assertNotIn('Tk', label)
                    self.assertNotIn('--group gui', command)
                    self.assertNotIn('verify-gui', command)

    def test_upload_cannot_overwrite_or_export_test_scratch(self):
        for section in (job('grouped'), job('short')):
            upload = step(section, 'Upload validation evidence')
            self.assertIn('if: always()', upload)
            self.assertIn('actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a', upload)
            self.assertIn('if-no-files-found: error', upload)
            self.assertIn('overwrite: false', upload)
            self.assertIn('include-hidden-files: false', upload)
            self.assertIn('!tmp/validation-evidence/**/temp/**', upload)
            self.assertNotIn('path: tmp\n', upload)
            self.assertNotIn('continue-on-error', section)
        self.assertIn('tmp/validation-evidence/**/pr-artifact.zip', job('short'))

    def test_short_path_uses_exact_dependency_lock_and_api_token_name(self):
        for section in (job('grouped'), job('short')):
            self.assertIn('python -m venv tmp/ci-venv', section)
            self.assertIn('-m pip install -r requirements-ci-lock.txt', section)
            self.assertNotIn('pip install --upgrade pip', section)
        short = job('short')
        self.assertIn('GITHUB_TOKEN: ${{ github.token }}', short)
        self.assertIn('validation_evidence.py post-merge', short)
        self.assertNotIn('pytest tests/', short)
        self.assertNotIn('Run GUI group', short)
        self.assertIn('cancel-in-progress: false', WORKFLOW)
        self.assertIn('github.event.pull_request.head.ref || github.ref', WORKFLOW)

    def test_legacy_exception_keeps_exact_original_identity_predicates(self):
        section = job('test')
        expression = re.search(r'^      PYTEST_ADDOPTS: (.*)$', section, re.M).group(1)
        expected = "${{ ((github.event_name == 'pull_request' && github.repository == 'discoveryray/zhuyin-proofreader' && github.event.pull_request.head.repo.full_name == 'discoveryray/zhuyin-proofreader' && github.event.pull_request.number == 29 && github.head_ref == 'codex/review-confirm-responsive' && github.event.pull_request.base.ref == 'develop' && github.event.pull_request.base.sha == '502414b3b38e004a6d8d9cb693cf21b65148765a') || (github.event_name == 'push' && github.repository == 'discoveryray/zhuyin-proofreader' && github.ref == 'refs/heads/develop' && github.event.before == '502414b3b38e004a6d8d9cb693cf21b65148765a')) && '--capture=sys' || '' }}"
        self.assertEqual(expression, expected)
        matrix = re.search(r'^        python-version: (.*)$', section, re.M).group(1)
        self.assertIn("github.head_ref == 'codex/simplify-validation' && github.event.pull_request.base.sha == '1593e7af65596d320b4427f1b15bb2bc0bdc949c'", matrix)
        for name in ('Run full unittest suite', 'Run full pytest suite', 'Verify active test execution'):
            condition = re.search(r'^        if: (.*)$', step(section, name), re.M).group(1)
            for required in ("github.event.pull_request.number == 29", "github.head_ref == 'codex/review-confirm-responsive'", "github.event.before == '502414b3b38e004a6d8d9cb693cf21b65148765a'", "github.head_ref == 'codex/simplify-validation'"):
                self.assertIn(required, condition)
        self.assertNotIn('37000834763', WORKFLOW)
        self.assertNotIn('457707b4c4109c1b10a0da76d8f8a884aca10341', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
