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

class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        subprocess.run(['git','init','-q'],cwd=self.root,check=True)
        subprocess.run(['git','config','user.email','test@example.invalid'],cwd=self.root,check=True)
        subprocess.run(['git','config','user.name','test'],cwd=self.root,check=True)
        (self.root/'app.py').write_text('print(1)\n')
        (self.root/'test_app.py').write_text('assert 1 == 1\n')
        subprocess.run(['git','add','app.py','test_app.py'],cwd=self.root,check=True)
        subprocess.run(['git','commit','-qm','baseline'],cwd=self.root,check=True)
        self.old=os.getcwd();os.chdir(self.root)
        class A: pass
        pipeline.cmd_init(A())
        pipeline.atomic_json(self.root/'.engineering-pipeline/checks.json', {
            'components':{
                'core':{'paths':['app.py','test_app.py'], 'depends_on':[],
                        'slice_checks':[['python3','-m','py_compile','app.py']]},
                'api':{'paths':['api/**'],'depends_on':['core'],
                       'slice_checks':[['python3','-c','print(\"api checked\")']]}
            },
            'full_checks':[['python3','-m','py_compile','app.py'],['python3','test_app.py']]
        })
        a=A();a.op='start';a.slug='good-task';a.kind='feature';a.size='small';a.architect='pipeline-architect-l2';pipeline.cmd_task(a)
        a=A();a.op='freeze-tests';a.slug='good-task';a.paths=['test_app.py'];pipeline.cmd_task(a)
    def tearDown(self):os.chdir(self.old);self.temp.cleanup()
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

if __name__=='__main__':unittest.main()
