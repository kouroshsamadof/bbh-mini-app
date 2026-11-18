import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deployment', Path(__file__).with_name('server.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
SHA = 'a' * 40
OLD = 'AZIZAM_IMAGE=azizam-bot:' + 'b' * 40 + '\n'


class DeploymentSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['shared', 'repository.git', 'releases/' + SHA]:
            (self.root / name).mkdir(parents=True)
        for name, value in [('ROOT', self.root), ('REPO', self.root / 'repository.git'),
                            ('SHARED', self.root / 'shared')]:
            p = patch.object(d, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.commands = []
        self.files = 'Dockerfile\nbot.py'
        self.head = SHA
        def fake_run(args, **kwargs):
            self.commands.append(args)
            if 'rev-parse' in args:
                return self.head
            if 'ls-tree' in args:
                return self.files
            if args[:3] == ['docker', 'image', 'inspect']:
                return json.dumps([{'Config': {'Healthcheck': {'Test': ['CMD', 'healthcheck']}}}])
            if 'ls-remote' in args:
                return SHA + '\trefs/heads/main'
            if 'fetch' in args:
                return ''
            raise AssertionError('Unexpected external command: ' + repr(args))
        p = patch.object(d, 'run', fake_run)
        p.start()
        self.addCleanup(p.stop)
        self.out = contextlib.redirect_stdout(io.StringIO())
        self.out.__enter__()
        self.addCleanup(self.out.__exit__, None, None, None)

    def test_frontend_only_commit_never_touches_containers(self):
        self.files = 'index.html\njs/app.js'
        d.deploy(SHA)
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['result'], 'waiting_for_bot_code')
        self.assertFalse(any(a[0] == 'docker' for a in self.commands))

    def test_outdated_commit_never_touches_containers(self):
        self.head = 'c' * 40
        d.deploy(SHA)
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['result'], 'skipped_outdated_commit')
        self.assertFalse(any(a[0] == 'docker' for a in self.commands))

    def test_missing_handover_flag_never_starts_bot_or_stops_observer(self):
        with patch.object(d, 'compose') as compose, patch.object(d, 'retire_observer') as retire:
            d.deploy(SHA)
            compose.assert_not_called()
            retire.assert_not_called()
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['result'], 'staged_waiting_for_first_handover')

    def activate(self):
        (self.root / 'shared/activation-ready').touch()
        p = self.root / 'shared/bot.env'
        p.write_text('TOKEN=private-test-value\n')
        p.chmod(0o600)

    def test_empty_private_settings_block_launch(self):
        self.activate()
        (self.root / 'shared/bot.env').write_text('# not configured\n')
        with patch.object(d, 'compose') as compose, self.assertRaises(RuntimeError):
            d.deploy(SHA)
        compose.assert_not_called()

    def test_failed_healthcheck_restores_previous_image_and_keeps_observer(self):
        self.activate()
        (self.root / 'current.env').write_text(OLD)
        calls = []
        def compose(envfile, *args, **kwargs):
            calls.append((envfile.name, args[0]))
            if envfile.name == 'candidate.env' and args[0] == 'up':
                raise RuntimeError('healthcheck failed')
        with patch.object(d, 'compose', compose), patch.object(d, 'backup_bot_data'), \
                patch.object(d, 'inspect_container', return_value=None), \
                patch.object(d, 'retire_observer') as retire:
            with self.assertRaisesRegex(RuntimeError, 'Release failed'):
                d.deploy(SHA)
            retire.assert_not_called()
        self.assertEqual(calls, [('current.env', 'stop'), ('candidate.env', 'up'), ('current.env', 'up')])
        self.assertEqual((self.root / 'current.env').read_text(), OLD)

    def test_observer_is_retired_only_after_healthy_startup(self):
        self.activate()
        events = []
        with patch.object(d, 'compose', side_effect=lambda *a, **k: events.append(a[1])), \
                patch.object(d, 'backup_bot_data'), patch.object(d, 'inspect_container', return_value=None), \
                patch.object(d, 'retire_observer', side_effect=lambda *a: events.append('retire')):
            d.deploy(SHA)
        self.assertEqual(events, ['stop', 'up', 'retire'])
        self.assertEqual((self.root / 'current.env').read_text(), 'AZIZAM_IMAGE=azizam-bot:' + SHA + '\n')

    def test_ssh_command_injection_is_rejected(self):
        with patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND': 'deploy ' + SHA + '; touch /tmp/bad'}), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            d.main()
        self.assertEqual(result.exception.code, 64)
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
