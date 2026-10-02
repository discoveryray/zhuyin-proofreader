"""Canonical local history fixtures: no GUI, network or actual task ledger writes."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import validation_runner as runner

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a' * 40
TASK = 'fixture-task'
SOURCE = 'fixture://original-user-instruction'


def block(value):
    return '<!-- validation-local-history\n' + json.dumps(value) + '\n-->'


class LocalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.common = self.root / 'repository' / '.git'
        self.common.mkdir(parents=True)
        self.git_patch = patch.object(runner, 'git', return_value=str(self.common))
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.env_patch = patch.dict(os.environ, {'GITHUB_ACTIONS': '', 'GITHUB_EVENT_NAME': ''})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def bootstrap(self):
        return runner.bootstrap_local(TASK, SOURCE, BASE)

    def test_common_git_directory_is_shared_across_checkouts_and_bad_identity_stops(self):
        expected = self.common.parent / 'tmp' / 'validation-task-ledgers' / TASK
        with patch.object(runner, 'ROOT', self.root / 'worktree-a'):
            first = runner.local_ledger(TASK)
        with patch.object(runner, 'ROOT', self.root / 'worktree-b'):
            second = runner.local_ledger(TASK)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        for invalid in ('../reset', '', 'SAME-TASK', 'task/name'):
            with self.subTest(task=invalid), self.assertRaises(ValueError):
                runner.local_ledger(invalid)

    def test_one_bootstrap_missing_corrupt_history_and_authorization_are_not_defaults(self):
        with self.assertRaises(ValueError):
            runner.bootstrap_local(TASK, '', BASE)
        with self.assertRaises(ValueError):
            runner.bootstrap_local(TASK, SOURCE, '0' * 40)
        folder = self.bootstrap()
        original = (folder / 'ledger.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.bootstrap()
        self.assertEqual((folder / 'ledger.json').read_bytes(), original)
        (folder / 'ledger.json').write_text('{"schema":"future"}')
        with self.assertRaises((ValueError, KeyError)):
            runner.export_local_history(TASK, self.root / 'export.json')
        (folder / 'ledger.json').unlink()
        with self.assertRaises(FileNotFoundError):
            runner.run_group('gui', self.root / 'another-output', task_id=TASK, mode='validation')
        self.assertFalse((folder / 'ledger.json').exists())

    def test_empty_export_is_original_raw_ledger_and_seals_every_output_path(self):
        folder = self.bootstrap()
        original = (folder / 'ledger.json').read_bytes()
        output = self.root / 'export.json'
        value = runner.export_local_history(TASK, output)
        self.assertTrue((folder / 'sealed.json').is_file())
        archive_bytes = base64.b64decode(value['archive_base64'], validate=True)
        self.assertEqual(hashlib.sha256(archive_bytes).hexdigest(), value['archive_sha256'])
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.namelist(), ['ledger.json'])
            self.assertEqual(archive.read('ledger.json'), original)
        imported = self.root / 'imported'
        imported.mkdir()
        runner.import_local_handoff(block(value), imported, TASK)
        self.assertEqual((imported / 'local-history/ledger.json').read_bytes(), original)
        with patch.object(runner, '_run_group') as execute:
            for destination in ('new-output', 'different-session-output'):
                with self.assertRaisesRegex(ValueError, 'sealed'):
                    runner.run_group('gui', self.root / destination, task_id=TASK, mode='validation')
            execute.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'sealed'):
            runner.export_local_history(TASK, self.root / 'second-export.json')

    def test_export_refuses_collision_and_size_without_erasing_prior_state(self):
        folder = self.bootstrap()
        existing = self.root / 'existing.json'
        existing.write_bytes(b'original output')
        with self.assertRaises(FileExistsError):
            runner.export_local_history(TASK, existing)
        self.assertEqual(existing.read_bytes(), b'original output')
        self.assertFalse((folder / 'sealed.json').exists())
        with patch.object(runner, 'HANDOFF_LIMIT', 5), self.assertRaisesRegex(ValueError, 'budget'):
            runner.export_local_history(TASK, self.root / 'large.json')
        self.assertFalse((folder / 'sealed.json').exists())

    def test_interrupted_export_stays_sealed_and_cannot_start_another_gui_attempt(self):
        folder = self.bootstrap()
        output = self.root / 'interrupted-output.json'
        real_write = runner.write_json
        def fail_output(path, data):
            if Path(path) == output:
                raise OSError('isolated output write failure')
            return real_write(path, data)
        with patch.object(runner, 'write_json', side_effect=fail_output), self.assertRaises(OSError):
            runner.export_local_history(TASK, output)
        self.assertTrue((folder / 'sealed.json').is_file())
        self.assertFalse(output.exists())
        with patch.object(runner, '_run_group') as execute, self.assertRaisesRegex(ValueError, 'sealed'):
            runner.run_group('gui', self.root / 'another-output', task_id=TASK, mode='validation')
        execute.assert_not_called()

    def test_missing_multiple_corrupt_and_cross_task_handoff_cannot_reset_history(self):
        self.bootstrap()
        value = runner.export_local_history(TASK, self.root / 'export.json')
        for kind in ('missing', 'multiple', 'corrupt', 'task'):
            with self.subTest(kind=kind):
                destination = self.root / kind
                destination.mkdir()
                data = dict(value)
                body = block(data)
                expected = TASK
                if kind == 'missing': body = 'no attestation'
                elif kind == 'multiple': body += body
                elif kind == 'corrupt':
                    data['archive_sha256'] = 'f' * 64
                    body = block(data)
                else: expected = 'other-task'
                with self.assertRaises(ValueError):
                    runner.import_local_handoff(body, destination, expected)
                self.assertFalse((destination / 'local-history').exists())

    def test_archive_cannot_include_live_database_or_escape_output(self):
        self.bootstrap()
        value = runner.export_local_history(TASK, self.root / 'export.json')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(value['archive_base64']))) as archive:
            ledger = archive.read('ledger.json')
        for unsafe in ('../outside', 'executions/id/live.sqlite', 'executions/id/temp/secret.json'):
            with self.subTest(path=unsafe):
                content = io.BytesIO()
                with zipfile.ZipFile(content, 'w') as archive:
                    archive.writestr('ledger.json', ledger)
                    archive.writestr(unsafe, b'not validation evidence')
                payload = content.getvalue()
                changed = dict(value, archive_sha256=hashlib.sha256(payload).hexdigest(), archive_base64=base64.b64encode(payload).decode())
                destination = self.root / ('import-' + str(len(list(self.root.iterdir()))))
                destination.mkdir()
                with self.assertRaises(ValueError):
                    runner.import_local_handoff(block(changed), destination, TASK)
                self.assertFalse((self.root / 'outside').exists())

    def test_real_cli_bootstrap_export_and_sealed_group_use_isolated_git_repository(self):
        clone = self.root / 'cli-repository'
        scripts = clone / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('validation_runner.py', 'test_entrypoint_audit.py'):
            shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
        subprocess.run(['git', 'init', str(clone)], check=True, capture_output=True)
        command = [sys.executable, str(scripts / 'validation_runner.py')]
        env = {**os.environ, 'GITHUB_ACTIONS': '', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        def cli(*args):
            return subprocess.run([*command, *args], cwd=clone, env=env, capture_output=True, timeout=20)
        first = cli('bootstrap-local', '--task-id', TASK, '--source-ref', SOURCE, '--baseline', BASE)
        self.assertEqual(first.returncode, 0, first.stderr)
        duplicate = cli('bootstrap-local', '--task-id', TASK, '--source-ref', SOURCE, '--baseline', BASE)
        self.assertEqual(duplicate.returncode, 2, duplicate.stderr)
        output = clone / 'export.json'
        exported = cli('export-local-history', '--task-id', TASK, '--output', str(output))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        attempted = cli('run', '--task-id', TASK, '--group', 'gui', '--mode', 'validation', '--evidence-root', str(clone / 'new-output'))
        self.assertEqual(attempted.returncode, 2, attempted.stderr)
        self.assertIn(b'sealed', attempted.stderr)
        self.assertFalse((clone / 'new-output').exists())


if __name__ == '__main__':
    unittest.main()
