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
spec=importlib.util.spec_from_file_location('pipeline',ROOT/'tools'/'pipeline.py')
pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)

class ModelTests(unittest.TestCase):
    def test_profiles_have_10_roles_and_independence(self):
        for profile in ('max-quality','balanced','budget'):
            self.assertTrue(pipeline.independent(json.loads((ROOT/'profiles'/f'{profile}.json').read_text())))
    def test_collision_rejected(self):
        x=pipeline.active_models();x['pipeline-code-a']='openai/gpt-6.1-sol'
        with self.assertRaises(pipeline.PipelineError):pipeline.independent(x)
    def test_gateway_not_family(self):
        self.assertEqual(pipeline.family('openrouter/moonshotai/kimi-k3.1-pro'),'kimi')
        self.assertEqual(pipeline.family('openrouter/z-ai/glm-5.3'),'glm')
        self.assertEqual(pipeline.family('openrouter/qwen/qwen3-coder-next'),'qwen')
    def test_agents_are_v2(self):
        for role in pipeline.ROLES:
            s=(ROOT/'agents'/f'{role}.md').read_text()
            self.assertIn('permissions:\n',s)
            self.assertNotIn('\npermission:\n',s)
            self.assertEqual(s.count('model: '),1)
    def test_orchestrator_has_safe_parallel_review_fallback(self):
        text=(ROOT/'agents'/'pipeline-orchestrator.md').read_text()
        self.assertIn('Launch A and B concurrently only',text)
        self.assertIn('Otherwise run A then B sequentially',text)
        self.assertIn('never to one review',text)
        self.assertNotIn('task-parallel',text)

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
                        'slice_checks':[['python3','-m','py_compile','app.py']]},
                'api':{'paths':['api/**'],'depends_on':['core'],
                       'slice_checks':[['python3','-c','print(\"api checked\")']]}
            },
            'full_checks':[['python3','-m','py_compile','app.py'],['python3','test_app.py']],
            'gate_cache':{
                'enabled':True,
                'dependency_files':['requirements.lock'],
                'config_files':['project.conf'],
                'environment':[['python3','--version']],
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
        state['design_approved_fingerprint']=pipeline.file_hash(design)
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
        self.assertIsNone(pipeline.require_analysis_context(self.root,state))

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

if __name__=='__main__':unittest.main()
