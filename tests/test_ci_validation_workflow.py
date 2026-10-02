"""Bounded workflow regressions; execute the real required-check PowerShell body.

No Actions run, GUI, Git write, credential request or functional suite is started.
"""
from pathlib import Path
import os
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


class ValidationWorkflowTests(unittest.TestCase):
    def test_required_check_executes_real_failure_sensitive_summary(self):
        body = step(job('test'), 'Verify required validation results')
        script = body.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        shell = shutil.which('pwsh') or shutil.which('powershell')
        self.assertIsNotNone(shell, 'Windows CI must provide the workflow PowerShell interpreter')
        cases = [
            ('pull_request', 'success', 'skipped', True),
            ('push', 'skipped', 'success', True),
            ('workflow_dispatch', 'success', 'success', False),
            ('pull_request', 'skipped', 'skipped', False),
            ('pull_request', 'success', 'success', False),
            ('push', 'success', 'success', False),
        ]
        for failure in ('failure', 'cancelled', 'timed_out', 'pending', ''):
            cases.extend([('pull_request', failure, 'skipped', False), ('push', 'skipped', failure, False)])
        for event, grouped, short, accepted in cases:
            with self.subTest(event=event, grouped=grouped, short=short):
                env = {**os.environ, 'EVENT_NAME': event, 'GROUPED_RESULT': grouped, 'SHORT_RESULT': short}
                result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
                                        env=env, capture_output=True, timeout=20)
                self.assertEqual(result.returncode == 0, accepted, (result.stdout + result.stderr).decode("utf-8", errors="replace"))
        self.assertIn('needs: [grouped, short]', job('test'))
        self.assertIn('if: always()', job('test'))
        self.assertIn('name: Python ${{ matrix.python-version }}', job('test'))

    def test_new_paths_keep_default_capture_and_independent_group_processes(self):
        grouped = job('grouped')
        for section in (grouped, job('short')):
            self.assertEqual(re.findall(r'^      PYTEST_ADDOPTS: (.*)$', section, re.M), ['""'])
            self.assertNotIn('--capture=sys', section)
        self.assertIn('python-version: \'3.13.0\'', grouped)
        for group in ('core', 'gui'):
            body = step(grouped, 'Run ' + ('GUI' if group == 'gui' else 'core') + ' group')
            self.assertIn('--group ' + group + ' --mode validation', body)
            self.assertNotIn('continue-on-error', body)
            self.assertNotIn('!cancelled()', body)
        self.assertLess(grouped.index('Restore validation history'), grouped.index('Run core group'))
        self.assertLess(grouped.index('Run core group'), grouped.index('Run GUI group'))
        self.assertLess(grouped.index('Run GUI group'), grouped.index('Aggregate functional coverage'))
        self.assertNotIn('python -m pytest tests/', grouped)
        self.assertNotIn('unittest discover', grouped)

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
        for name in ('Run full unittest suite', 'Run full pytest suite', 'Verify GUI test execution'):
            condition = re.search(r'^        if: (.*)$', step(section, name), re.M).group(1)
            for required in ("github.event.pull_request.number == 29", "github.head_ref == 'codex/review-confirm-responsive'", "github.event.before == '502414b3b38e004a6d8d9cb693cf21b65148765a'", "github.head_ref == 'codex/simplify-validation'"):
                self.assertIn(required, condition)
        self.assertNotIn('37000834763', WORKFLOW)
        self.assertNotIn('457707b4c4109c1b10a0da76d8f8a884aca10341', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
