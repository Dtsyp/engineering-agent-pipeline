import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import host_config
import routing


class RoutingTests(unittest.TestCase):
    def test_inherited_and_explicit_policy(self):
        for host in routing.HOSTS:
            policy = routing.profile_policy('inherit', host)
            self.assertEqual(set(policy['models'].values()), {None})
            routing.validate_review_models(policy, [None] * 3)
        for preset in routing.PRESETS[1:]:
            routing.validate_routes(routing.profile_policy(preset, 'opencode'))
        policy = routing.profile_policy('max-quality', 'opencode')
        models = policy['models']
        with self.assertRaisesRegex(ValueError, 'collision'):
            routing.validate_review_models(policy, [models['pipeline-code-a']] * 3)
        with self.assertRaisesRegex(ValueError, 'explicit declared'):
            routing.validate_review_models(policy, [None] * 3)
        with self.assertRaisesRegex(ValueError, 'OpenCode selectors'):
            routing.profile_policy('budget', 'codex')

    def test_quoted_description_and_horizontal_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.md'
            description = 'Reviews "A": на C:\\work --- with # tags'
            body = 'First paragraph\n\n---\n\nSecond paragraph'
            text = '---\ndescription: ' + json.dumps(description, ensure_ascii=False) + '\n---\n' + body
            path.write_text(text, encoding='utf8')
            self.assertEqual(host_config.read_role(path), (description, body))

    def test_orchestrator_has_fresh_review_fallback(self):
        _, text = host_config.agent_parts('pipeline-orchestrator')
        self.assertIn('Launch A and B concurrently only', text)
        self.assertIn('Otherwise run A then B sequentially', text)
        self.assertIn('never to one review', text)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='engpipe % тест & ')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home), PYTHONIOENCODING='utf-8',
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        CLAUDE_CONFIG_DIR=str(self.home / '.claude'), CODEX_HOME=str(self.home / '.codex'),
                        ENGPIPE_CONFIG_HOME=str(self.home / 'pipeline settings'),
                        ENGPIPE_BIN_DIR=str(self.home / 'pipeline bin'))

    def cli(self, *args, ok=True):
        proc = subprocess.run([sys.executable, str(ROOT / 'tools/pipeline.py'), *args],
                              env=self.env, capture_output=True, encoding='utf8')
        self.assertEqual(proc.returncode == 0, ok, proc.stderr + proc.stdout)
        return proc

    @unittest.skipUnless(yaml, 'install tests/requirements.txt for actual YAML parsing')
    def test_all_native_exports_and_reinstall(self):
        old_rules = self.home / '.config/opencode/AGENTS.md'
        old_rules.parent.mkdir(parents=True)
        old_rules.write_text('local instructions', encoding='utf8')
        config = old_rules.with_name('opencode.jsonc')
        config.write_text('{/*keep*/ "default_agent":"mine"}', encoding='utf8')
        unrelated = old_rules.parent / 'agents' / 'pipeline-organization.md'
        unrelated.parent.mkdir()
        unrelated.write_text('local agent', encoding='utf8')
        def header(path):
            return yaml.safe_load(path.read_text(encoding='utf8').split('\n---\n', 1)[0])
        for host in routing.HOSTS:
            self.cli('install', '--target', host)
            self.cli('install', '--target', host)
        self.assertEqual(old_rules.read_text(), 'local instructions')
        self.assertEqual(config.read_text(), '{/*keep*/ "default_agent":"mine"}')
        self.assertEqual(unrelated.read_text(), 'local agent')
        self.assertFalse((self.home / 'pipeline settings/backups').exists())
        with mock.patch.dict(os.environ, self.env):
            for role in routing.ROLES:
                expected = header(ROOT / 'agents' / (role + '.md'))
                self.assertNotIn('model', expected)
                oc = header(host_config.role_file('opencode', role))
                cc = header(host_config.role_file('claude', role))
                cx = tomllib.loads(host_config.role_file('codex', role).read_text(encoding='utf8'))
                self.assertEqual([node['description'] for node in (oc, cc, cx)], [expected['description']] * 3)
                for node in (oc, cc, cx):
                    self.assertNotIn('model', node)
                self.assertEqual(cc['name'], role)
                self.assertEqual(cx['name'], role)
                if role in routing.REVIEWERS:
                    self.assertEqual(oc['mode'], 'all')
                    self.assertEqual(cc['tools'], 'Read, Glob, Grep')
                    self.assertFalse(cx['features']['request_permissions_tool'])
            skill = host_config.skill_path()
            self.assertEqual(header(skill)['name'], 'pipeline-orchestrator')
            exe, _ = host_config.launcher()
        command = [str(exe), 'profile', 'list']
        if os.name == 'nt':
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', *command]
        run = subprocess.run(command, env=self.env, capture_output=True, encoding='utf8')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('inherit', run.stdout)

    def test_native_installer_entry(self):
        if os.name == 'nt':
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                       str(ROOT / 'install.ps1'), '-Target', 'codex']
        else:
            command = ['sh', str(ROOT / 'install.sh'), 'codex']
        run = subprocess.run(command, env=self.env, cwd=self.home, capture_output=True, encoding='utf8')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.home / '.codex/agents/pipeline-orchestrator.toml').is_file())

    def test_launcher_preserves_argument_boundaries(self):
        checkout = self.home / "checkout ' % &"
        tools = checkout / 'tools'
        tools.mkdir(parents=True)
        shutil.copy2(ROOT / 'tools/windows-launcher.ps1', tools)
        (tools / 'pipeline.py').write_text('import sys,json; print(json.dumps(sys.argv[1:]))', encoding='utf8')
        with mock.patch.dict(os.environ, self.env), mock.patch.object(host_config, 'REPO', checkout):
            exe, script = host_config.launcher()
        host_config.atomic_text(exe, script)
        values = ['кириллица', '', 'with space', '100% & | < > !', 'a"b', "a'b", 'end' + chr(92)]
        if os.name == 'nt':
            def literal(value):
                return "'" + value.replace("'", "''") + "'"
            expression = '& ' + literal(str(exe)) + ' ' + ' '.join(literal(value) for value in values)
            encoded = base64.b64encode(expression.encode('utf-16le')).decode('ascii')
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]
        else:
            exe.chmod(0o755)
            command = [str(exe), *values]
        run = subprocess.run(command, env=self.env, capture_output=True, encoding='utf8')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), values)

    def test_model_update_only_changes_selected_host(self):
        canonical = {p: p.read_bytes() for p in (ROOT / 'agents').glob('*.md')}
        self.cli('install', '--target', 'codex')
        self.cli('model', 'set', 'pipeline-code-a', 'local-model', '--family', 'local', '--target', 'codex')
        target = self.home / '.codex/agents/pipeline-code-a.toml'
        self.assertEqual(tomllib.loads(target.read_text(encoding='utf8'))['model'], 'local-model')
        overlay = json.loads(self.cli('model', 'list', '--target', 'codex').stdout)
        self.assertEqual(overlay['families'], {'local-model': 'local'})
        self.assertIsNone(json.loads(self.cli('model', 'list', '--target', 'opencode').stdout)['models']['pipeline-code-a'])
        self.assertEqual({p: p.read_bytes() for p in canonical}, canonical)
        self.cli('profile', 'use', 'inherit', '--target', 'codex')
        self.assertNotIn('model', tomllib.loads(target.read_text(encoding='utf8')))

    def test_collision_and_migration_policy(self):
        path = self.home / '.claude/agents/pipeline-code-b.md'
        path.parent.mkdir(parents=True)
        path.write_text('customized', encoding='utf8')
        old = path.parent / '.engineering-backup-20261101010101'
        old.mkdir()
        (old / path.name).write_text('older', encoding='utf8')
        self.cli('install', '--target', 'claude', ok=False)
        self.assertFalse(path.with_name('pipeline-analyst.md').exists())
        self.assertEqual(path.read_text(), 'customized')
        self.cli('install', '--target', 'claude', '--replace')
        self.assertFalse(old.exists())
        self.assertIn('older', [p.read_text(encoding='utf8') for p in (self.home / 'pipeline settings/backups').rglob(path.name)])
        backup_files = (self.home / 'pipeline settings/backups').rglob('*-' + path.name)
        self.assertEqual(next(backup_files).read_text(), 'customized')

    def test_install_messages_respect_output_encoding(self):
        self.cli('install', '--target', 'opencode')
        with mock.patch.dict(os.environ, self.env):
            for encoding, preset in (('cp1252', 'budget'), ('utf8', 'inherit')):
                with self.subTest(encoding=encoding):
                    output = io.BytesIO()
                    with io.TextIOWrapper(output, encoding=encoding) as stream:
                        with contextlib.redirect_stdout(stream):
                            host_config.update_policy('opencode', routing.profile_policy(preset, 'opencode'))
                        stream.flush()
                        messages = output.getvalue().decode(encoding)
                    self.assertIn('Backup directory:', messages)
                    expected = str(self.home).encode(encoding, errors='backslashreplace').decode(encoding)
                    self.assertIn(expected, messages)
                    self.assertEqual(routing.task_policy('opencode'), routing.profile_policy(preset, 'opencode'))
                    self.assertFalse(host_config.journal_path('opencode').exists())
                    host_config.verify_installed('opencode')

    def test_interrupted_routing_update_recovers_without_overwriting_edits(self):
        self.cli('install', '--target', 'opencode')
        with mock.patch.dict(os.environ, self.env):
            original = host_config.atomic_text
            def interrupted(path, value):
                if path.name == 'pipeline-code-a.md':
                    raise OSError('interrupted write')
                original(path, value)
            with mock.patch.object(host_config, 'atomic_text', side_effect=interrupted):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    host_config.update_policy('opencode', routing.profile_policy('budget', 'opencode'))
            with self.assertRaisesRegex(ValueError, 'interrupted installation'):
                host_config.verify_installed('opencode')
            path = host_config.role_file('opencode', 'pipeline-code-a')
            previous = path.read_bytes()
            path.write_text('user edit', encoding='utf8')
            self.cli('install', '--target', 'opencode', ok=False)
            self.assertEqual(path.read_text(), 'user edit')
            path.write_bytes(previous)
            self.cli('install', '--target', 'opencode')
            self.assertEqual(routing.task_policy('opencode'), routing.profile_policy('budget', 'opencode'))
            host_config.verify_installed('opencode')

    def test_doctor_fails_for_absent_host_and_changed_artifacts(self):
        self.cli('install', '--target', 'claude')
        self.env['PATH'] = str(self.home)
        self.assertIn('executable not found', self.cli('doctor', '--target', 'claude', ok=False).stderr)
        p = self.home / '.claude/agents/pipeline-analyst.md'
        p.write_text('changed', encoding='utf8')
        self.assertIn('role missing or stale', self.cli('doctor', '--target', 'claude', ok=False).stderr)


if __name__ == '__main__':
    unittest.main()
