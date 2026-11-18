import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec=importlib.util.spec_from_file_location('pipeline',ROOT/'tools'/'pipeline.py')
pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)

class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old_cache_env=os.environ.get('ENGPIPE_TEST_ENV');os.environ['ENGPIPE_TEST_ENV']='stable'
        subprocess.run(['git','init','-q'],cwd=self.root,check=True)
        subprocess.run(['git','config','user.email','test@example.invalid'],cwd=self.root,check=True)
        subprocess.run(['git','config','user.name','test'],cwd=self.root,check=True)
        (self.root/'app.py').write_text('print(1)\n')
        (self.root/'test_app.py').write_text('assert 1 == 1\n')
        (self.root/'requirements.lock').write_text('example==1\n')
        (self.root/'project.conf').write_text('mode=test\n')
        subprocess.run(['git','add','app.py','test_app.py','requirements.lock','project.conf'],cwd=self.root,check=True)
        subprocess.run(['git','commit','-qm','baseline'],cwd=self.root,check=True)
        self.old=os.getcwd();os.chdir(self.root)
        class A: pass
        pipeline.cmd_init(A())
        pipeline.atomic_json(self.root/'.engineering-pipeline/checks.json', {
            'components':{
                'core':{'paths':['app.py','test_app.py','requirements.lock','project.conf'], 'depends_on':[],
                        'slice_checks':[[sys.executable,'-m','py_compile','app.py']]},
                'api':{'paths':['api/**'],'depends_on':['core'],
                       'slice_checks':[[sys.executable,'-c','print(\"api checked\")']]}
            },
            'full_checks':[[sys.executable,'-m','py_compile','app.py'],[sys.executable,'test_app.py']],
            'gate_cache':{
                'enabled':True,
                'dependency_files':['requirements.lock'],
                'config_files':['project.conf'],
                'environment':[[sys.executable,'--version']],
                'env_vars':['ENGPIPE_TEST_ENV']
            }
        })
        a=A();a.op='start';a.slug='good-task';a.kind='feature';a.size='small';a.architect='pipeline-architect-l2';pipeline.cmd_task(a)
        a=A();a.op='preflight';a.slug='good-task';a.goal='Preserve behavior';a.in_scope=['app.py'];a.out_of_scope=['public API'];a.constraint=['no new dependencies'];a.acceptance=['tests pass'];a.decision=['keep current output'];a.blocking_question=[];pipeline.cmd_task(a)
        a=A();a.op='analysis-context';a.slug='good-task';a.evidence=['app.py:1 | current behavior prints one'];a.component=['core'];a.test=['test_app.py'];a.risk=[];a.decision=['reuse current entry point'];a.unknown=[];pipeline.cmd_task(a)
        a=A();a.op='freeze-tests';a.slug='good-task';a.paths=['test_app.py'];pipeline.cmd_task(a)
    def tearDown(self):
        os.chdir(self.old)
        if self.old_cache_env is None:os.environ.pop('ENGPIPE_TEST_ENV',None)
        else:os.environ['ENGPIPE_TEST_ENV']=self.old_cache_env
        self.temp.cleanup()
    def action(self,op,**kw):
        class A:pass
        a=A();a.op=op;a.slug='good-task'
        if op in ('code-review','design-review'):
            stage = op.split('-')[0]
            if 'against' not in kw:
                kw['against'] = pipeline.review_input(self.root,pipeline.get_state(self.root,a.slug),stage)['against']
            kw.setdefault('checked',['contract behavior in app.py and test_app.py'])
        for k,v in kw.items():setattr(a,k,v)
        if not hasattr(a,'findings'): a.findings=''
        return pipeline.cmd_task(a)
    def test_slice_targets_upstream_and_downstream_without_full(self):
        # A change to core triggers its own check and the dependent API check.
        (self.root/'app.py').write_text('print(2)\n')
        self.action('slice-check')
        state=pipeline.get_state(self.root,'good-task')
        rec=state['slice_checks'][-1]
        self.assertEqual(rec['status'],'PASS')
        self.assertEqual(rec['components'],['api','core'])
        self.assertEqual(len(rec['commands']),2)
        self.assertFalse(state['full_gate'])
        with self.assertRaises(pipeline.PipelineError):self.action('finish')
        # Another slice is checked against the last successful slice snapshot.
        (self.root/'api').mkdir()
        (self.root/'api/client.py').write_text('from app import price\n')
        self.action('slice-check')
        state=pipeline.get_state(self.root,'good-task')
        self.assertEqual(state['slice_checks'][-1]['components'],['api'])
        self.assertFalse(state['full_gate'])
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session=None)
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session=None)
        self.action('finish')

    def test_slice_unknown_path_fails_closed_without_full_run(self):
        (self.root/'mystery.py').write_text('print(42)\n')
        with self.assertRaisesRegex(pipeline.PipelineError,'unmapped changed paths'):
            self.action('slice-check')
        self.assertFalse(pipeline.get_state(self.root,'good-task')['full_gate'])

    def test_slice_failed_check_keeps_baseline_and_can_retry(self):
        state=pipeline.get_state(self.root,'good-task')
        baseline=state['slice_snapshot']
        (self.root/'app.py').write_text('syntax error !\n')
        with self.assertRaisesRegex(pipeline.PipelineError,'slice checks FAIL'):
            self.action('slice-check')
        state=pipeline.get_state(self.root,'good-task')
        self.assertEqual(state['slice_snapshot'],baseline)
        self.assertEqual(state['slice_checks'][-1]['status'],'FAIL')
        (self.root/'app.py').write_text('print(42)\n')
        self.action('slice-check')
        self.assertEqual(pipeline.get_state(self.root,'good-task')['slice_checks'][-1]['status'],'PASS')

    def test_two_pass_and_stale_diff(self):
        self.action('gate')
        with self.assertRaises(pipeline.PipelineError):self.action('finish')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session='sess-a')
        with self.assertRaises(pipeline.PipelineError):self.action('finish')
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session='sess-b')
        (self.root/'app.py').write_text('print(2)\n')
        with self.assertRaises(pipeline.PipelineError):self.action('finish')
        with self.assertRaisesRegex(pipeline.PipelineError,'slice-check'):self.action('gate')
        self.action('slice-check')
        self.action('gate')
        with self.assertRaises(pipeline.PipelineError):self.action('finish')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session='sess-c')
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session='sess-d')
        self.action('finish')
        self.assertEqual(pipeline.get_state(self.root,'good-task')['status'],'DONE')
    def test_optional_review_session_ids(self):
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session=None)
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session=None)
        self.action('finish')
        state=pipeline.get_state(self.root,'good-task')
        self.assertEqual(state['status'],'DONE')
        self.assertFalse(state['session_audit']['code_ids_present'])

    def test_one_review_session_missing(self):
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session=None)
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session='real-b')
        self.action('finish')
        self.assertFalse(pipeline.get_state(self.root,'good-task')['session_audit']['code_ids_present'])

    def test_optional_design_session_ids(self):
        state=pipeline.get_state(self.root,'good-task')
        state['size']='large'
        pipeline.save_state(self.root,'good-task',state)
        design=self.root/'.engineering-pipeline/tasks/good-task/design.md'
        design.write_text('Architecture: keep existing API; add tests\n',encoding='utf8')
        self.action('design-review',focus='a',verdict='PASS',blockers=0,session=None)
        self.action('design-review',focus='b',verdict='PASS',blockers=0,session=None)
        state=pipeline.get_state(self.root,'good-task')
        state['design_approved_fingerprint']=pipeline.review_input(self.root,state,'design')['against']
        pipeline.save_state(self.root,'good-task',state)
        self.assertIsNone(pipeline.require_design(self.root,state))
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session=None)
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session=None)
        self.action('finish')
        self.assertFalse(pipeline.get_state(self.root,'good-task')['session_audit']['design_ids_present'])

    def test_freeze_blocks_test_changes(self):
        (self.root/'test_app.py').write_text('assert True\n')
        with self.assertRaises(pipeline.PipelineError):self.action('gate')
    def test_same_session_rejected(self):
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session='same')
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session='same')
        with self.assertRaises(pipeline.PipelineError):self.action('finish')

    def test_new_task_requires_preflight_and_evidence_context(self):
        class A:pass
        a=A();a.op='start';a.slug='new-task';a.kind='bugfix';a.size='small';a.architect='pipeline-architect-l2';pipeline.cmd_task(a)
        state=pipeline.get_state(self.root,'new-task')
        self.assertEqual(state['status'],'PREFLIGHT')
        with self.assertRaisesRegex(pipeline.PipelineError,'preflight'):
            pipeline.require_analysis_context(self.root,state)
        a=A();a.op='preflight';a.slug='new-task';a.goal='fix';a.in_scope=['app.py'];a.out_of_scope=[];a.constraint=[];a.acceptance=['regression passes'];a.decision=[];a.blocking_question=['expected error semantics?'];pipeline.cmd_task(a)
        with self.assertRaisesRegex(pipeline.PipelineError,'preflight'):
            pipeline.require_analysis_context(self.root,pipeline.get_state(self.root,'new-task'))

    def test_legacy_in_flight_task_remains_recoverable(self):
        state=pipeline.get_state(self.root,'good-task')
        state.pop('workflow_version');state.pop('preflight');state.pop('analysis_context')
        pipeline.save_state(self.root,'good-task',state)
        with self.assertRaisesRegex(pipeline.PipelineError, 'migrate'):
            self.action('gate')
        self.action('migrate',host='codex')
        migrated=pipeline.get_state(self.root,'good-task')
        self.assertEqual(migrated['status'],'PREFLIGHT')
        self.assertEqual(migrated['migration_frozen_tests'],state['frozen_tests'])
        self.assertTrue((self.root/'.engineering-pipeline/tasks/good-task/legacy-v1/state.json').is_file())
        with self.assertRaisesRegex(pipeline.PipelineError, 'preflight'):
            self.action('gate')

    def test_analysis_context_requires_path_line_evidence(self):
        state=pipeline.get_state(self.root,'good-task');state['analysis_context']={};state['frozen_tests']={}
        pipeline.save_state(self.root,'good-task',state)
        with self.assertRaisesRegex(pipeline.PipelineError,'path:line'):
            self.action('analysis-context',evidence=['unsupported summary'],component=[],test=[],risk=[],decision=[],unknown=[])

    def test_full_gate_reuses_only_identical_validation_context(self):
        with mock.patch.object(pipeline,'run_checks',wraps=pipeline.run_checks) as run:
            self.action('gate')
            self.action('gate')
            self.assertEqual(run.call_count,1)
        state=pipeline.get_state(self.root,'good-task')
        self.assertEqual(state['full_gate']['reuse_count'],1)
        self.assertTrue(state['full_gate']['cacheable'])

    def test_full_gate_reruns_when_environment_identity_changes(self):
        with mock.patch.object(pipeline,'run_checks',wraps=pipeline.run_checks) as run:
            self.action('gate')
            os.environ['ENGPIPE_TEST_ENV']='changed'
            self.action('gate')
            self.assertEqual(run.call_count,2)

    def test_review_rejects_gate_after_environment_identity_changes(self):
        self.action('gate')
        os.environ['ENGPIPE_TEST_ENV']='changed'
        with self.assertRaisesRegex(pipeline.PipelineError,'validation context changed'):
            self.action('code-review',focus='a',verdict='PASS',blockers=0,session='review-a')

    def test_full_gate_reruns_after_dependency_change_and_slice_check(self):
        with mock.patch.object(pipeline,'run_checks',wraps=pipeline.run_checks) as run:
            self.action('gate')
            (self.root/'requirements.lock').write_text('example==2\n')
            with self.assertRaisesRegex(pipeline.PipelineError,'slice-check'):
                self.action('gate')
            self.action('slice-check')
            self.action('gate')
            self.assertEqual(run.call_count,3)  # initial gate, targeted slice, new full gate

    def test_full_gate_reruns_when_checks_configuration_changes(self):
        with mock.patch.object(pipeline,'run_checks',wraps=pipeline.run_checks) as run:
            self.action('gate')
            path=self.root/'.engineering-pipeline/checks.json';cfg=json.loads(path.read_text())
            cfg['gate_cache']['env_vars'].append('ENGPIPE_SECOND_ENV')
            pipeline.atomic_json(path,cfg)
            self.action('gate')
            self.assertEqual(run.call_count,2)

    def test_full_gate_reruns_when_evidence_log_is_modified(self):
        with mock.patch.object(pipeline,'run_checks',wraps=pipeline.run_checks) as run:
            self.action('gate')
            (self.root/'.engineering-pipeline/tasks/good-task/full-gate.log').write_text('tampered\n')
            self.action('gate')
            self.assertEqual(run.call_count,2)

    def cli(self, *arguments, ok=True, cwd=None):
        result = subprocess.run([sys.executable, str(ROOT/'tools/pipeline.py'), *arguments],
                                cwd=cwd or self.root, capture_output=True, encoding='utf8', timeout=30)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_cache_off_rejects_lost_log_changed_manifest_and_stale_reviews(self):
        path = self.root/'.engineering-pipeline/checks.json'
        config = pipeline.read_json(path)
        config['gate_cache']['enabled'] = False
        pipeline.atomic_json(path, config)
        self.action('gate')
        self.action('code-review', focus='a', verdict='PASS', blockers=0, session='actual-a')
        self.action('code-review', focus='b', verdict='PASS', blockers=0, session='actual-b')
        td = pipeline.tdir(self.root,'good-task')
        (td/'full-gate.log').unlink()
        self.cli('task','finish','good-task',ok=False)
        config['full_checks'] = [[sys.executable,'-c','raise SystemExit(1)']]
        pipeline.atomic_json(path, config)
        self.cli('task','review-input','good-task','code',ok=False)
        self.cli('task','gate','good-task',ok=False)
        self.assertEqual(pipeline.get_state(self.root,'good-task')['code_reviews'], {})

    def test_finish_checks_source_after_probe_exits(self):
        manifest = self.root/'.engineering-pipeline/checks.json'
        config = pipeline.read_json(manifest)
        command = ("from pathlib import Path; p=Path('app.py'); "
                   "marker=Path('.engineering-pipeline/tasks/mutate-probe'); "
                   "p.write_text('print(0)') if marker.exists() else None")
        config['gate_cache']['environment'] = [[sys.executable,'-c',command]]
        pipeline.atomic_json(manifest, config)
        self.action('gate')
        for focus in 'ab':
            self.action('code-review',focus=focus,verdict='PASS',blockers=0,session=focus)
        (self.root/'.engineering-pipeline/tasks/mutate-probe').touch()
        self.cli('task','finish','good-task',ok=False)
        self.assertNotEqual(pipeline.get_state(self.root,'good-task')['status'],'DONE')

    def test_check_cannot_change_its_own_manifest_with_cache_off(self):
        path = self.root/'.engineering-pipeline/checks.json'
        config = pipeline.read_json(path)
        config['gate_cache']['enabled'] = False
        config['full_checks'] = [[sys.executable, '-c',
            "with open('.engineering-pipeline/checks.json', 'a') as stream: stream.write(' ')"]]
        pipeline.atomic_json(path, config)
        self.cli('task','gate','good-task',ok=False)
        self.assertEqual(pipeline.get_state(self.root,'good-task')['full_gate']['status'], 'FAIL')

    def test_fix_blocks_old_generation_and_requires_changed_subject(self):
        self.action('gate')
        original = pipeline.review_input(self.root,pipeline.get_state(self.root,'good-task'),'code')['against']
        self.action('code-review', focus='a', verdict='PASS', blockers=0, session='a')
        self.action('code-review', focus='b', verdict='FIX', blockers=1, session='b', findings=json.dumps([
            {'severity':'BLOCKER','location':'app.py:1','evidence':'prints 1','impact':'wrong output','suggested_fix':'print 2'}]))
        self.cli('task','review-input','good-task','code',ok=False)
        self.cli('task','gate','good-task')
        self.cli('task','review-input','good-task','code',ok=False)
        (self.root/'app.py').write_text('print(2)\n', encoding='utf8')
        self.action('slice-check')
        self.action('gate')
        self.cli('task','code-review','good-task','a','PASS','0','--against',original,'--checked','old input',ok=False)
        self.assertEqual(pipeline.get_state(self.root,'good-task')['code_reviews'], {})
        self.cli('task','finish','good-task',ok=False)

    def test_preflight_integrity_and_early_slice(self):
        self.cli('task','start','early-task','--kind','feature','--size','small')
        directory = pipeline.tdir(self.root,'early-task')
        before = (directory/'state.json').read_bytes()
        (self.root/'app.py').write_text('print(42)\n', encoding='utf8')
        self.cli('task','slice-check','early-task',ok=False)
        self.assertEqual(before, (directory/'state.json').read_bytes())
        existing = pipeline.tdir(self.root,'good-task')/'preflight.json'
        existing.write_text('{}',encoding='utf8')
        self.cli('task','slice-check','good-task',ok=False)
        self.cli('task','gate','good-task',ok=False)

    def test_freeze_cannot_replace_accepted_set_and_analysis_citations_exist(self):
        self.cli('task','freeze-tests','good-task','test_app.py','app.py',ok=False)
        self.cli('task','start','cited-task','--kind','feature','--size','small')
        self.cli('task','preflight','cited-task','--goal','Keep contract','--in-scope','app.py','--acceptance','test_app.py')
        for evidence in ('missing.py:1 | source','app.py:999 | source','../outside.py:1 | source'):
            self.cli('task','analysis-context','cited-task','--evidence',evidence,ok=False)
        self.cli('task','analysis-context','cited-task','--evidence','app.py:1 | source')
        (self.root/'new_test.py').write_text('assert 1\n',encoding='utf8')
        self.cli('task','freeze-tests','cited-task','new_test.py')
        self.cli('task','freeze-tests','cited-task','test_app.py',ok=False)

    def test_impacts_typo_and_git_mode_and_tracked_cache(self):
        config_path = self.root/'.engineering-pipeline/checks.json'
        config = pipeline.read_json(config_path)
        config['components']['api']['depends_on'] = ['cor']
        pipeline.atomic_json(config_path, config)
        self.cli('task','gate','good-task',ok=False)
        cache = self.root/'coverage'
        cache.mkdir()
        (cache/'contract.txt').write_text('contract v1',encoding='utf8')
        subprocess.run(['git','add','-f','coverage/contract.txt'],cwd=self.root,check=True)
        before = pipeline.fingerprint(self.root)
        (cache/'contract.txt').write_text('contract v2',encoding='utf8')
        self.assertNotEqual(before,pipeline.fingerprint(self.root))
        before = pipeline.fingerprint(self.root)
        subprocess.run(['git','update-index','--chmod=+x','app.py'],cwd=self.root,check=True)
        self.assertNotEqual(before,pipeline.fingerprint(self.root))

    def test_linked_worktree_private_inputs_and_dirty_baseline(self):
        import validation
        link = self.root/'linked'
        subprocess.run(['git','worktree','add','--detach',str(link),'HEAD'],cwd=self.root,check=True,capture_output=True)
        self.cli('init',cwd=link)
        (link/'private-untracked.txt').write_text('uncommitted input',encoding='utf8')
        (link/'app.py').write_text('print(99)\n',encoding='utf8')
        refs = pipeline.git(link,'for-each-ref')
        self.cli('task','start','linked-task','--kind','feature','--size','small',cwd=link)
        self.assertEqual(refs,pipeline.git(link,'for-each-ref'))
        td=pipeline.tdir(link,'linked-task')
        tree=pipeline.get_state(link,'linked-task')['baseline_tree']
        self.assertNotEqual(subprocess.run(['git','cat-file','-e',tree],cwd=link,capture_output=True).returncode,0)
        for name,expected in [('private-untracked.txt',b'uncommitted input'),('app.py',b'print(99)\n')]:
            out=subprocess.run(['git','show',tree+':'+name],cwd=link,env=validation.tree_environment(td),
                               capture_output=True,check=True)
            self.assertEqual(out.stdout,expected)
        listed=pipeline.git(link,'ls-files','--others','--exclude-standard').decode()
        self.assertIn('.engineering-pipeline/checks.json',listed)
        self.assertNotIn('/tasks/',listed)

    def test_gate_and_reviews_survive_checkout_move(self):
        import shutil
        self.action('gate')
        self.action('code-review',focus='a',verdict='PASS',blockers=0,session='a')
        self.action('code-review',focus='b',verdict='PASS',blockers=0,session='b')
        with tempfile.TemporaryDirectory() as directory:
            moved=Path(directory)/'moved repo'
            shutil.copytree(self.root,moved)
            self.cli('task','finish','good-task',cwd=moved)

    def test_design_approval_rechecks_bytes_after_human_input(self):
        state=pipeline.get_state(self.root,'good-task')
        state['size']='large'
        pipeline.save_state(self.root,'good-task',state)
        design=pipeline.tdir(self.root,'good-task')/'design.md'
        design.write_text('approved candidate\n',encoding='utf8')
        for focus in 'ab':
            self.action('design-review',focus=focus,verdict='PASS',blockers=0,session=focus)
        self.cli('task','approve-design','good-task',ok=False)
        def response(_):
            design.write_text('unexpected change\n',encoding='utf8')
            return 'APPROVE good-task'
        with mock.patch.object(sys.stdin,'isatty', return_value=True),mock.patch('builtins.input',side_effect=response):
            with self.assertRaisesRegex(pipeline.PipelineError,'changed'):
                self.action('approve-design')
        self.assertFalse(pipeline.get_state(self.root,'good-task').get('design_approved_fingerprint'))

    def test_submodule_changes_and_bounded_binary_output(self):
        import validation
        # The Git index declares a gitlink but its worktree has not been initialized.
        head=pipeline.git(self.root,'rev-parse','HEAD').decode().strip()
        subprocess.run(['git','update-index','--add','--cacheinfo','160000,'+head+',nested'],
                       cwd=self.root, check=True)
        with self.assertRaisesRegex(ValueError,'initialize the submodule'):
            pipeline.file_snapshot(self.root)
        log=pipeline.tdir(self.root,'good-task')/'binary.log'
        self.assertTrue(validation.run_checks(self.root,[[sys.executable,'-c',
                        "import sys; sys.stdout.buffer.write(b'\\xff' * 400000)"]],log))
        self.assertLessEqual(log.stat().st_size, 3*validation.OUTPUT_LIMIT)


    def test_untracked_sources_and_named_outputs_follow_git_ignore(self):
        self.action('gate')
        original = pipeline.fingerprint(self.root)
        for name in ('coverage/business.go','node_modules/handwritten.py','build.tsbuildinfo','.eslintcache'):
            path = self.root/name
            path.parent.mkdir(exist_ok=True)
            path.write_text('new\n',encoding='utf8')
            self.assertIn(name,pipeline.file_snapshot(self.root))
            self.assertNotEqual(original,pipeline.fingerprint(self.root))
            self.cli('task','review-input','good-task','code',ok=False)
            path.unlink()
        generated = self.root/'__pycache__'/'manual.py'
        generated.parent.mkdir(exist_ok=True)
        generated.write_text('manual source\n',encoding='utf8')
        self.assertIn('__pycache__/manual.py',pipeline.file_snapshot(self.root))
        (self.root/'.gitignore').write_text('coverage/\n',encoding='utf8')
        (self.root/'coverage/business.go').write_text('ignored\n',encoding='utf8')
        self.assertNotIn('coverage/business.go',pipeline.file_snapshot(self.root))

    def test_index_only_manifest_change_invalidates_current_gate(self):
        manifest = self.root/'.engineering-pipeline/checks.json'
        original = manifest.read_bytes()
        subprocess.run(['git','add','--','.engineering-pipeline/checks.json'],cwd=self.root,check=True)
        self.action('gate')
        token=pipeline.review_input(self.root,pipeline.get_state(self.root,'good-task'),'code')['against']
        for focus in 'ab':
            self.action('code-review',focus=focus,verdict='PASS',blockers=0,session=focus,against=token)
        cfg=json.loads(original)
        cfg['full_checks'] = [[sys.executable,'-c','raise SystemExit(1)']]
        pipeline.atomic_json(manifest,cfg)
        subprocess.run(['git','add','--','.engineering-pipeline/checks.json'],cwd=self.root,check=True)
        manifest.write_bytes(original)
        self.cli('task','finish','good-task',ok=False)
        self.cli('task','gate','good-task')
        state=pipeline.get_state(self.root,'good-task')
        current=pipeline.review_input(self.root,state,'code')['against']
        self.assertNotEqual(current,token)
        self.assertRegex(state['full_gate']['manifest']['index']['oid'],r'^[0-9a-f]+$')
        self.cli('task','code-review','good-task','a','PASS','0','--checked','old input','--against',token,ok=False)

    def test_native_and_host_reject_contradictory_structured_findings(self):
        from review_contract import validate_report
        self.action('gate')
        token=pipeline.review_input(self.root,pipeline.get_state(self.root,'good-task'),'code')['against']
        finding={'severity':'MAJOR','location':'app.py:1','evidence':'observed output','impact':'wrong result',
                 'suggested_fix':'fix implementation'}
        for severity in ('MAJOR','BLOCKER'):
            finding['severity']=severity
            report=dict(against=token,verdict='PASS',blockers=0,checked=['app.py:1'],findings=[finding])
            with self.assertRaisesRegex(ValueError,'PASS contradicts'):
                validate_report(report,token)
            self.cli('task','code-review','good-task','a','PASS','0','--against',token,'--checked','app.py:1',
                     '--findings',json.dumps([finding]),ok=False)
        finding['severity']='MINOR'
        self.cli('task','code-review','good-task','a','PASS','0','--against',token,'--checked','app.py:1',
                 '--findings',json.dumps([finding]))
        self.assertEqual([finding],pipeline.get_state(self.root,'good-task')['code_reviews']['a']['findings'])

    def test_large_task_cannot_absorb_post_start_source_before_approval(self):
        path = self.root/'app.py'
        path.write_text('print("pre-existing dirty input")\n',encoding='utf8')
        original=path.read_bytes()
        self.cli('task','start','large-task','--kind','feature','--size','large','--host','codex')
        path.write_text('print("premature implementation")\n',encoding='utf8')
        self.cli('task','preflight','large-task','--goal','Preserve contract','--in-scope','app.py','--acceptance','tests')
        self.cli('task','analysis-context','large-task','--evidence','app.py:1 | source',ok=False)
        self.cli('task','review-input','large-task','design',ok=False)
        path.write_bytes(original)
        self.cli('task','analysis-context','large-task','--evidence','app.py:1 | source')
        td=pipeline.tdir(self.root,'large-task')
        (td/'design.md').write_text('Update behavior after approval',encoding='utf8')
        self.cli('task','review-input','large-task','design')
        path.write_text('print("late premature implementation")\n',encoding='utf8')
        self.cli('task','review-input','large-task','design',ok=False)

    def test_migration_full_tree_contains_dirty_and_committed_prior_work(self):
        import validation
        state = pipeline.get_state(self.root,'good-task')
        state['workflow_version'] = 2
        state['slice_snapshot']['deleted-before-migration.py'] = 'old digest'
        pipeline.save_state(self.root,'good-task',state)
        (self.root/'app.py').write_bytes(b'print("already committed")\n')
        subprocess.run(['git','commit','-qam','earlier implementation'],cwd=self.root,check=True)
        (self.root/'new.py').write_bytes(b'print("still dirty")\n')
        self.cli('task','migrate','good-task','--host','codex')
        state = pipeline.get_state(self.root,'good-task')
        self.assertEqual('full-repository-after-migration',state['baseline_kind'])
        self.assertIn('deleted-before-migration.py',state['migration_missing_paths'])
        td=pipeline.tdir(self.root,'good-task')
        current=validation.worktree_tree(self.root,td)
        patch=subprocess.run(['git','diff',state['baseline_tree'],current],cwd=self.root,env=validation.tree_environment(td),
                             capture_output=True,check=True)
        self.assertIn(b'print("already committed")',patch.stdout)
        self.assertIn(b'print("still dirty")',patch.stdout)


if __name__=='__main__':unittest.main()
