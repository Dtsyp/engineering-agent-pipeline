"""Offline host protocol fixture. This file never contacts a model service."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

HOST, *ARGS = sys.argv[1:]
CLI = os.environ['FIXTURE_CLI']
CONTROL = Path(os.environ['FIXTURE_CONTROL'])
FLAG = (CONTROL / 'behavior').read_text()
if ARGS[:2] == ['debug', 'agents']:
    import yaml
    directory = Path(os.environ['XDG_CONFIG_HOME']) / 'opencode' / 'agents'
    roles = []
    for entry in directory.glob('pipeline-*.md'):
        header = yaml.safe_load(entry.read_text(encoding='utf8').split('---', 2)[1])
        record = {'id': entry.stem, 'mode': header['mode'], 'permissions': header['permissions']}
        if header.get('model'):
            provider, selected = header['model'].split('/', 1)
            model, _, variant = selected.partition('#')
            record['model'] = {'providerID':provider,'id':model,'variant':variant}
        roles.append(record)
    print(json.dumps(roles))
    sys.exit(0)
if 'mcp' in ARGS and 'list' in ARGS:
    print('[{"name":"example"}]')
    sys.exit(0)
if HOST == 'opencode':
    prompt = Path(ARGS[ARGS.index('--file') + 1]).read_text(encoding='utf8')
else:
    prompt = sys.stdin.read()
coordinator = re.search(r'Console coordinator for existing task ([a-z0-9-]+). Active stage: (code|design)', prompt)
review = re.search(r'Review task ([a-z0-9-]+), stage (code|design), against ([a-f0-9]+)', prompt)
slug, stage = (coordinator or review).groups()[:2]
if HOST == 'codex':
    role = 'pipeline-orchestrator' if coordinator else re.search(r'Role: (pipeline-[a-z-]+)', prompt)[1]
    if review:
        assert '--sandbox' in ARGS and ARGS[ARGS.index('--sandbox') + 1] == 'read-only'
        assert any('example' in x and 'enabled=false' in x for x in ARGS)
else:
    role = ARGS[ARGS.index('--agent') + 1]
    if HOST == 'claude' and review:
        assert ARGS[ARGS.index('--tools') + 1] == 'Read,Glob,Grep'
trace = CONTROL / (uuid.uuid4().hex + '.event')
trace.write_text(json.dumps({'host': HOST, 'role': role, 'stage': stage, 'slug': slug,
                            'review': bool(review), 'token': review.group(3) if review else None,
                            'model': ARGS[ARGS.index('--model')+1] if '--model' in ARGS else None}))
def task(op, *args):
    subprocess.run([sys.executable, CLI, 'task', op, slug, *args], check=True,
                   stdout=subprocess.DEVNULL)
td = Path('.engineering-pipeline/tasks') / slug
if coordinator:
    state = json.loads((td / 'state.json').read_text(encoding='utf8'))
    if FLAG == 'poison':
        state['status'] = 'DONE'
        (td / 'state.json').write_text(json.dumps(state))
    elif FLAG == 'hold':
        (CONTROL / 'writer-started').touch()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (CONTROL / 'release').exists():
            time.sleep(0.05)
        sys.exit(27)
    elif FLAG == 'exit':
        sys.exit(23)
    elif FLAG != 'pretend':
        if not state['preflight']:
            task('preflight', '--goal', 'Verify client', '--in-scope', 'app.py', '--acceptance', 'contract passes')
        if not state['analysis_context']:
            task('analysis-context', '--evidence', 'app.py:1 | current service', '--test', 'test_app.py')
        if stage == 'design':
            (td / 'design.md').write_text('Plan based on the existing contract.')
        else:
            if not state['frozen_tests']:
                task('freeze-tests', 'test_app.py')
            with open('app.py', 'a', encoding='utf8') as output:
                output.write('# implemented slice\n')
            Path('untracked.py').write_text('print(42)\n')
            if FLAG == 'localcommit':
                subprocess.run(['git','add','app.py','untracked.py'],check=True)
                subprocess.run(['git','commit','-qm','local task commit'],check=True)
            task('slice-check')
            task('gate')
    answer = 'DONE'
else:
    if FLAG == 'empty':
        sys.exit(0)
    token = review.group(3)
    path = re.search(r'Read the complete shared inputs at ([^,]+)', prompt)[1]
    packet = json.loads(Path(path).read_text(encoding='utf8'))
    assert packet['against'] == token and packet['routing']['host'] == HOST and packet['base_commit']
    if stage == 'code':
        assert 'untracked.py' in Path(packet['worktree_patch']).read_text(encoding='utf8')
        assert Path(packet['gate_log']).is_file() and packet['frozen_tests']
    if FLAG == 'partial' and role.endswith('-b'):
        sys.exit(13)
    if FLAG == 'stale' and role.endswith('-a'):
        with open('app.py', 'a') as f:
            f.write('# changed during review\n')
    findings = []
    verdict = 'PASS'
    if FLAG == 'fix' and not (CONTROL / 'fixed').exists() and role.endswith('-a'):
        verdict = 'FIX'
        (CONTROL / 'fixed').touch()
        findings.append(dict(severity='MAJOR', location='app.py:1', evidence='missing case',
                             impact='wrong output', suggested_fix='fix case'))
    answer = json.dumps(dict(against=token, verdict=verdict, blockers=0, findings=findings,
                             checked=['app.py:1 and accepted behavior']))
    if FLAG == 'bad':
        answer = 'PASS'
session = uuid.uuid4().hex
if HOST == 'codex':
    print(json.dumps({'type':'thread.started', 'thread_id':session}))
    print(json.dumps({'type':'turn.started'}))
    print(json.dumps({'type':'item.completed', 'item':{'type':'agent_message','text':answer}}))
    if not (FLAG == 'truncated' and review):
        print(json.dumps({'type':'turn.completed', 'usage':{'input_tokens':1,'output_tokens':1}}))
elif HOST == 'opencode':
    print(json.dumps({'type':'text', 'sessionID':session, 'part':{'type':'text','text':answer}}))
else:
    print(json.dumps({'type':'result', 'subtype':'success', 'result':answer, 'session_id':session}))
