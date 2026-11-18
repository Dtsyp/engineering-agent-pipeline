"""Black-box regression for two independent reviews against one real Git working tree."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'tools' / 'pipeline.py'


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / 'application with spaces'
        self.project.mkdir()
        self.env = {**os.environ, 'ENGPIPE_CONFIG_HOME': str(Path(temp.name) / 'local preferences'),
                    'PYTHONUTF8': '1', 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': str(Path(temp.name) / 'gitconfig')}
        self.slug = 'invoice-fix'
        self.check = [sys.executable, 'test_invoice.py']
        self.config = {'components': {'app': {'paths': ['*.py'], 'depends_on': [],
                                              'slice_checks': [self.check]}},
                       'full_checks': [self.check]}
        (self.project / 'invoice.py').write_text('def amount(): return 1\n', encoding='utf8')
        (self.project / 'test_invoice.py').write_text(
            'from invoice import amount\nassert amount() > 0\n', encoding='utf8')
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Test', '-c',
                     'user.email=test@example.invalid', 'commit', '-qm', 'baseline']):
            subprocess.run(['git', *args], cwd=self.project, env=self.env, check=True)
        self.cli('init')
        self.manifest = self.project / '.engineering-pipeline/checks.json'
        self.manifest.write_text(json.dumps(self.config), encoding='utf8')
        self.task('start', '--kind', 'bugfix', '--size', 'small', '--host', 'codex')
        self.task('preflight', '--goal', 'Keep invoice amount positive',
                  '--in-scope', 'invoice.py', '--acceptance', 'test_invoice.py passes')
        self.task('analysis-context', '--evidence', 'invoice.py:1 | amount returns the invoice amount',
                  '--component', 'app', '--test', 'test_invoice.py')
        self.task('freeze-tests', 'test_invoice.py')
        (self.project / 'invoice.py').write_text('def amount(): return 2\n', encoding='utf8')
        self.task('slice-check')
        self.task('gate')

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=self.project,
                                env=self.env, capture_output=True, text=True, encoding='utf8', timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def task(self, command, *args, success=True):
        return self.cli('task', command, self.slug, *args, success=success)

    def token(self):
        return json.loads(self.task('review-input', 'code').splitlines()[-1])['against']

    def review(self, focus, token, success=True):
        self.task('code-review', focus, 'PASS', '0', '--session', 'session-' + focus,
                  '--against', token, '--checked', 'Verified positive amount against test_invoice.py',
                  success=success)

    def test_new_gate_requires_both_fresh_reports_even_with_unchanged_code(self):
        first = self.token()
        self.review('a', first)
        self.review('b', first)
        self.config['full_checks'].append([sys.executable, '-c', 'import invoice; assert invoice.amount() == 2'])
        self.manifest.write_text(json.dumps(self.config), encoding='utf8')
        self.task('finish', success=False)
        self.task('gate')
        self.task('finish', success=False)
        second = self.token()
        self.assertNotEqual(first, second)
        self.review('a', first, success=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            results = [workers.submit(self.review, focus, second) for focus in ('a', 'b')]
            for completed in results:
                completed.result()
        self.task('finish')
        status = json.loads(self.task('status'))
        self.assertEqual(status['task']['status'], 'DONE')
        self.assertEqual(set(status['task']['code_reviews']), {'a', 'b'})


class InstallerEntrypointTests(unittest.TestCase):
    def test_os_installer_and_installed_command_from_nonstandard_path(self):
        import shutil

        source = CLI.parents[1]
        with tempfile.TemporaryDirectory(prefix='engpipe test ') as temp:
            directory = Path(temp)
            checkout = directory / 'проект & 100%'
            checkout.mkdir()
            for name in ('tools', 'profiles', 'agents'):
                shutil.copytree(source / name, checkout / name,
                                ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('install.ps1', 'install.sh'):
                shutil.copy2(source / name, checkout / name)
            home = directory / 'test user'
            home.mkdir()
            bindir = home / 'bin'
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8='1',
                       ENGPIPE_CONFIG_HOME=str(home / 'settings'), ENGPIPE_BIN_DIR=str(bindir),
                       CLAUDE_CONFIG_DIR=str(home / 'claude'))
            if os.name == 'nt':
                shell = shutil.which('pwsh') or shutil.which('powershell')
                self.assertIsNotNone(shell)
                install = [shell, '-NoProfile', '-File', str(checkout / 'install.ps1'), '-Target', 'claude']
            else:
                install = ['sh', str(checkout / 'install.sh'), 'claude']
            sub = subprocess.run(install, cwd=home, env=env, capture_output=True, timeout=30)
            self.assertEqual(sub.returncode, 0, (sub.stdout, sub.stderr))
            if os.name == 'nt' and (bindir / 'engpipe.ps1').exists():
                run = [shell, '-NoProfile', '-File', str(bindir / 'engpipe.ps1')]
            elif os.name == 'nt':
                run = [str(bindir / 'engpipe.cmd')]
            else:
                run = [str(bindir / 'engpipe')]
            sub = subprocess.run([*run, 'profile', 'list'], cwd=home, env=env,
                                 capture_output=True, timeout=30)
            self.assertEqual(sub.returncode, 0, (sub.stdout, sub.stderr))
            self.assertIn(b'inherit', sub.stdout)
