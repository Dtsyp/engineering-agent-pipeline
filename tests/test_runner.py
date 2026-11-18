"""Offline subprocess integration tests: native host protocol doubles + real engpipe."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools' / 'pipeline.py'
FAKE = ROOT / 'tests' / 'fixtures' / 'runner_host.py'
sys.path.insert(0, str(ROOT / 'tools'))
import runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.project = base / 'project with spaces'
        self.project.mkdir()
        self.control = base / 'fixture state'
        self.control.mkdir()
        self.behavior('pass')
        user = base / 'test home'
        user.mkdir()
        self.bin = base / 'host programs'
        self.bin.mkdir()
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'HOME': str(user), 'USERPROFILE': str(user), 'XDG_CONFIG_HOME': str(user / '.config'),
                    'CODEX_HOME': str(user / '.codex'), 'CLAUDE_CONFIG_DIR': str(user / '.claude'),
                    'ENGPIPE_CONFIG_HOME': str(user / 'config'), 'ENGPIPE_BIN_DIR': str(user / 'bin'),
                    'FIXTURE_CONTROL': str(self.control), 'FIXTURE_CLI': str(CLI),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        for host in ('codex', 'claude', 'opencode'):
            self.make_host(host)
            self.cli('install', '--target', host)
        self.git('init', '--quiet')
        self.git('config', 'user.email', 'runner@example.invalid')
        self.git('config', 'user.name', 'runner')
        (self.project / 'app.py').write_text('print(1)\n')
        (self.project / 'test_app.py').write_text('assert 1 == 1\n')
        self.cli('init')
        check = [sys.executable, 'test_app.py']
        settings = dict(components=dict(app=dict(paths=['*.py'], depends_on=[], slice_checks=[check])),
                        full_checks=[check])
        (self.project / '.engineering-pipeline/checks.json').write_text(json.dumps(settings))
        self.git('add', '.')
        self.git('commit', '-qm', 'Initial contract')

    def make_host(self, host):
        if os.name == 'nt':
            # Shim deliberately fails; the runner must execute the npm target with native node.
            if not shutil.which('node.exe'):
                self.skipTest('Windows fake npm CLI integration requires node.exe')
            name = {'opencode':'@opencode/cli', 'claude':'@anthropic-ai/claude-code', 'codex':'@openai/codex'}[host]
            package = self.bin / 'node_modules' / name
            package.mkdir(parents=True)
            (self.bin / (host + '.cmd')).write_text('@exit /b 99\n')
            (package / 'package.json').write_text(json.dumps({'name':name, 'bin':{host:'cli.js'}}))
            (package / 'cli.js').write_text(
                "const {spawnSync} = require('child_process');\n"
                "const p = spawnSync(" + json.dumps(sys.executable) + ", [" + json.dumps(str(FAKE))
                + ", " + json.dumps(host) + ", ...process.argv.slice(2)], {stdio:'inherit'});\n"
                "process.exit(p.status === null ? 32 : p.status);\n")
        else:
            program = self.bin / host
            program.write_text('#!/usr/bin/env python3\nimport os, sys\nos.execv('
                               + repr(sys.executable) + ', [' + repr(sys.executable) + ', '
                               + repr(str(FAKE)) + ', ' + repr(host) + ', *sys.argv[1:]])\n')
            program.chmod(0o755)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.project, env=self.env, capture_output=True, check=True)

    def cli(self, *args, code=0, cwd=None):
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=cwd or self.project, env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, encoding='utf8', timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + '\n' + result.stderr)
        return result

    def start(self, slug='new-task', host='codex', size='small', **kwargs):
        return self.cli('run', slug, '--size', size, '--host', host,
                        '--prompt', 'Deliver & verify "odd %PATH%" and $(echo literal).', **kwargs)

    def behavior(self, value):
        (self.control / 'behavior').write_text(value)

    def events(self, slug, kind):
        events = [json.loads(p.read_text()) for p in self.control.glob('*.event')]
        return [e for e in events if e['slug'] == slug and e['review'] == kind]

    def state(self, slug):
        return json.loads((self.project / '.engineering-pipeline/tasks' / slug / 'state.json').read_text())

    def test_each_host_uses_separate_sessions_and_shared_input(self):
        for host in ('codex', 'claude', 'opencode'):
            slug = 'case-' + host
            if host != 'codex':
                (self.project / 'untracked.py').unlink()
            self.start(slug, host)
            reviews = self.state(slug)['code_reviews']
            self.assertEqual(self.state(slug)['status'], 'DONE')
            self.assertNotEqual(reviews['a']['session'], reviews['b']['session'])
            self.assertEqual(reviews['a']['against'], reviews['b']['against'])
            self.cli('run', slug, '--resume')
            self.assertEqual(2, len(self.events(slug, True)))

    def test_explicit_opencode_selector_and_post_start_commit(self):
        selected = 'example/local-model#high'
        self.cli('model', 'set', 'pipeline-code-a', selected, '--target', 'opencode')
        self.behavior('localcommit')
        self.start(host='opencode')
        models = {e['role']: e['model'] for e in self.events('new-task', True)}
        self.assertEqual({'pipeline-code-a':selected, 'pipeline-code-b':None}, models)
        self.assertEqual(self.state('new-task')['status'], 'DONE')

    def test_noninteractive_approval_pauses_and_skips_repeated_design_reviews(self):
        self.start(size='large', code=3)
        self.cli('run', 'new-task', '--resume', code=3)
        self.assertEqual(2, len(self.events('new-task', True)))
        self.assertFalse(self.state('new-task').get('design_approved_fingerprint'))
        self.assertFalse(self.state('new-task')['frozen_tests'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX terminal line discipline')
    def test_console_eof_leaves_reviewed_design_pending(self):
        import pty

        self.start(size='large', code=3)
        reviews = self.state('new-task')['design_reviews']
        master, terminal = pty.openpty()
        try:
            # The real terminal reports isatty() true while its next read returns EOF.
            os.write(master, b'\x04')
            result = subprocess.run([sys.executable, str(CLI), 'run', 'new-task', '--resume'],
                                    cwd=self.project, env=self.env, stdin=terminal, capture_output=True,
                                    encoding='utf8', timeout=30)
        finally:
            os.close(master)
            os.close(terminal)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('PAUSED', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(reviews, self.state('new-task')['design_reviews'])
        self.assertFalse(self.state('new-task').get('design_approved_fingerprint'))

    def test_partial_and_malformed_reviews_do_not_publish_a_pass(self):
        for behavior in ('partial', 'empty', 'bad', 'truncated', 'stale'):
            with self.subTest(behavior=behavior):
                slug = 'case-' + behavior
                self.behavior(behavior)
                if (self.project / 'untracked.py').exists():
                    (self.project / 'untracked.py').unlink()
                self.start(slug, code=2)
                self.assertFalse(self.state(slug)['code_reviews'])

    def test_fix_repairs_and_rereviews_both(self):
        self.behavior('fix')
        self.start()
        self.assertEqual(len(self.events('new-task', True)), 4)
        self.assertEqual(self.state('new-task')['status'], 'DONE')

    def test_false_success_is_not_completion(self):
        self.behavior('pretend')
        result = self.start(code=2)
        self.assertIn('is not ready', result.stderr)
        self.assertNotEqual(self.state('new-task')['status'], 'DONE')

    def test_unauthorized_done_remains_quarantined_after_recovery(self):
        self.behavior('poison')
        self.start(code=2)
        marker = self.project / '.engineering-pipeline/tasks/.runner-intent.json'
        attempt = json.loads(marker.read_text())['attempt']
        self.cli('run', 'new-task', '--resume', code=2)
        self.cli('run', 'new-task', '--recover-attempt', attempt)
        self.cli('run', 'new-task', '--resume', code=2)
        self.assertTrue((marker.parent / 'new-task/quarantine.json').is_file())

    def test_crashed_driver_blocks_other_tasks_but_not_linked_worktrees(self):
        self.behavior('hold')
        writer = subprocess.Popen([sys.executable, str(CLI), 'run', 'first-task', '--host', 'codex',
                                   '--size', 'small', '--prompt', 'test'], env=self.env, cwd=self.project,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(120):
                if (self.control / 'writer-started').exists():
                    break
                time.sleep(0.05)
            self.assertTrue((self.control / 'writer-started').exists())
            self.assertIn('already in progress', self.start('another-task', code=2).stderr)
            writer.kill()
            writer.wait(timeout=5)
            (self.control / 'release').touch()
            self.assertIn('uncertain writer', self.start('another-task', code=2).stderr)
            self.behavior('pass')
            linked = self.project.parent / 'linked'
            self.git('worktree', 'add', '--quiet', '--detach', str(linked))
            self.start('linked-task', cwd=linked)
            attempt = json.loads((self.project / '.engineering-pipeline/tasks/.runner-intent.json').read_text())['attempt']
            self.cli('run', 'first-task', '--recover-attempt', 'wrong', code=2)
            self.cli('run', 'first-task', '--recover-attempt', attempt)
            self.cli('run', 'first-task', '--resume')
        finally:
            (self.control / 'release').touch()
            if writer.poll() is None:
                writer.kill()
            writer.wait(timeout=10)

    def test_runner_validates_git_exclusion_before_storing_prompt_or_lock(self):
        private = self.project / '.engineering-pipeline/tasks'
        (self.project / '.gitignore').write_text(
            '!.engineering-pipeline/tasks/\n.engineering-pipeline/tasks/*\n'
            '!.engineering-pipeline/tasks/new-task/\n.engineering-pipeline/tasks/new-task/*\n'
            '!.engineering-pipeline/tasks/new-task/runner.json\n')
        self.assertIn('private', self.start(code=2).stderr)
        self.assertFalse((private / '.runner-worktree.lock').exists())
        self.assertFalse((private / 'new-task').exists())

    def test_runner_rejects_linked_storage_before_private_writes(self):
        outside = self.project.parent / 'user-folder'
        outside.mkdir()
        link = self.project / '.engineering-pipeline/tasks/new-task'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlink unavailable for this Windows account')
        self.assertIn('symlink', self.start(code=2).stderr)
        self.assertEqual([], list(outside.iterdir()))
        self.assertFalse((link.parent / '.runner-worktree.lock').exists())

    def test_adopting_one_current_native_pass_dispatches_only_missing_focus(self):
        slug = 'handoff'
        self.cli('task','start',slug,'--host','codex','--size','small','--kind','feature')
        self.cli('task','preflight',slug,'--goal','preserve contract','--in-scope','app.py',
                 '--acceptance','contract passes')
        self.cli('task','analysis-context',slug,'--evidence','app.py:1 | service')
        self.cli('task','freeze-tests',slug,'test_app.py')
        (self.project / 'untracked.py').write_text('print(42)\n')
        self.cli('task','slice-check',slug)
        self.cli('task','gate',slug)
        token = json.loads(self.cli('task','review-input',slug,'code').stdout.splitlines()[-1])['against']
        self.cli('task','code-review',slug,'a','PASS','0','--against',token,'--checked','native-only evidence',
                 '--session','real-native-reviewer')
        previous = self.state(slug)['code_reviews']['a']
        self.cli('run',slug,'--resume','--prompt','Portable adoption')
        self.cli('run',slug,'--resume')
        self.assertEqual(previous, self.state(slug)['code_reviews']['a'])
        self.assertEqual(['pipeline-code-b'], [e['role'] for e in self.events(slug,True)])
        self.assertEqual(self.state(slug)['status'], 'DONE')

    def test_legacy_packet_exposes_full_tree_committed_work_and_known_deletions(self):
        slug = 'legacy-case'
        self.cli('task','start',slug,'--host','codex','--size','small','--kind','feature')
        directory = self.project / '.engineering-pipeline/tasks' / slug
        state = self.state(slug)
        state['workflow_version'] = 2
        state['slice_snapshot']['gone.py'] = 'old'
        (directory / 'state.json').write_text(json.dumps(state))
        (self.project / 'before_migration.py').write_text('print("before migration")\n')
        self.git('add','before_migration.py')
        self.git('commit','-qm','pre-migration local implementation')
        self.cli('task','migrate',slug,'--host','codex')
        self.cli('run',slug,'--resume','--prompt','Review the migrated task')
        packet = json.loads(next(directory.glob('code-*.json')).read_text(encoding='utf8'))
        self.assertEqual(packet['baseline_kind'], 'full-repository-after-migration')
        self.assertIn('gone.py', packet['migration_missing_paths'])
        text = (self.project / packet['worktree_patch']).read_text(encoding='utf8')
        self.assertIn('before_migration.py', text)
        self.assertIn('test_app.py', text)
        self.assertIn('index',packet['manifest_identity'])

    def test_codex_requires_ordered_successful_terminal_events(self):
        thread = {'type':'thread.started', 'thread_id':'native-session'}
        start = {'type':'turn.started'}
        message = {'type':'item.completed','item':{'type':'agent_message','text':'review'}}
        done = {'type':'turn.completed'}
        for events in ([thread,start,message], [thread,start,start,message,done], [thread,start,message,done,done],
                       [thread,start,message,done,{'type':'turn.failed'}], [thread,start,thread,message,done],
                       [thread,start,message,{'type':'turn.completed','thread_id':'other'}]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                runner.host_reply('codex',io.BytesIO(b'\n'.join(json.dumps(e).encode() for e in events)))

    def test_core_must_see_current_feedback_even_if_repeated_gate_passes(self):
        token = '012345'
        text = json.dumps({'against':token, 'verdict':'PASS', 'blockers':0, 'findings':[], 'checked':['app.py:1']})
        self.assertEqual('PASS', runner.validated_report(text, token)['verdict'])
        with self.assertRaisesRegex(ValueError, 'input identifier'):
            runner.validated_report(text, 'new')


if __name__ == '__main__':
    unittest.main()
