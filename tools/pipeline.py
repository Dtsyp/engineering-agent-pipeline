#!/usr/bin/env python3
"""Portable OpenCode engineering pipeline configuration and deterministic gates; stdlib only.
This is a deterministic LOCAL gate, not proof of actual LLM session identity.
"""
import argparse
import fnmatch
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import uuid

# Script execution and importlib-based tooling both resolve the sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import routing
from review_contract import validate_report
from storage import atomic_text, file_lock, verify_private_paths
from validation import capture_command, file_hash, file_snapshot, git, run_checks, worktree_tree

import host_config

REPO = Path(__file__).resolve().parents[1]
from routing import ROLES, ARCHS, REVIEWERS
NAME_PATTERN = re.compile(r'^[a-z][a-z0-9-]{1,60}$')
WORKFLOW_VERSION = 3

class PipelineError(Exception): pass

def die(message): raise PipelineError(message)

def read_json(p, default=None):
    if not p.exists():
        if default is None: die('missing file: '+str(p))
        return default
    return json.loads(p.read_text(encoding='utf8'))

def atomic_json(p, data):
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_text(p, text.encode('utf8', errors='backslashreplace').decode('utf8') + '\n')

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()

def json_hash(value):
    encoded=json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('utf8')
    return hashlib.sha256(encoded).hexdigest()

def project_root():
    raw = git(Path.cwd(), 'rev-parse', '--show-toplevel').rstrip(b'\r\n')
    return Path(os.fsdecode(raw)).resolve()

def tdir(root,slug):
    if not NAME_PATTERN.fullmatch(slug):die('invalid slug: use lowercase Latin, numbers and hyphens')
    return root/'.engineering-pipeline'/'tasks'/slug

def task_json(root,slug):return tdir(root,slug)/'state.json'

def get_state(root,slug):return read_json(task_json(root,slug))

def save_state(root,slug,data):atomic_json(task_json(root,slug),data)

def fingerprint(root, snapshot=None):
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    return json_hash([head, snapshot if snapshot is not None else file_snapshot(root)])


def changed_files_since(before, after):
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def validate_command(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(arg, str) and arg for arg in value):
        die(f'invalid argv in {where}; expected ["program", "arg", ...]')
    return value


def load_checks(root):
    path = root / '.engineering-pipeline' / 'checks.json'
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not isinstance(cfg, dict) or not isinstance(cfg.get('components'), dict):
        die('checks.json must declare components')
    components = cfg['components']
    for name, entry in components.items():
        if not isinstance(entry, dict):
            die('invalid component: ' + name)
        for key in ('paths', 'depends_on'):
            items = entry.get(key, [] if key == 'depends_on' else None)
            if not isinstance(items, list) or not all(isinstance(x, str) and x for x in items):
                die(name + '.' + key + ' must be a list of strings')
        if not entry['paths']:
            die(name + ' must define at least one ownership pattern')
        unknown = set(entry.get('depends_on', [])) - components.keys()
        if unknown:
            die(name + ' has unknown dependencies: ' + ', '.join(sorted(unknown)))
        commands = entry.get('slice_checks')
        if not isinstance(commands, list) or not commands:
            die(name + ' must declare slice_checks')
        for command in commands:
            validate_command(command, name)
    full = cfg.get('full_checks')
    if not isinstance(full, list):
        die('full_checks must be a list')
    for command in full:
        validate_command(command, 'full_checks')
    return cfg, manifest_identity(root, raw)


def manifest_identity(root, content=None):
    name = '.engineering-pipeline/checks.json'
    if content is None:
        content = (root / name).read_bytes()
    raw = git(root, 'ls-files', '--stage', '-z', '--', name)
    records = [line.partition(b'\t')[0].decode('ascii').split() for line in raw.split(b'\0') if line]
    index = None
    if records:
        if len(records) != 1 or records[0][2] != '0':
            die('resolve the Git index conflict for ' + name)
        mode, oid, stage = records[0]
        index = {'mode': mode, 'oid': oid, 'stage': stage}
    return {'worktree_sha256': hashlib.sha256(content).hexdigest(), 'index': index}


def checks_config(root):
    return load_checks(root)[0]


def require_preflight(s, root=None):
    if s.get('workflow_version', 1) != WORKFLOW_VERSION:
        die('legacy task; run engpipe task migrate ' + s['slug'])
    pre = s.get('preflight', {})
    if pre.get('status') != 'READY':
        die('preflight is incomplete or has blocking questions')
    root = root if root is not None else project_root()
    if regular_hash(tdir(root, s['slug']) / 'preflight.json') != pre.get('fingerprint'):
        die('preflight artifact changed; restore the recorded artifact or record preflight again')


def regular_hash(path):
    if path.is_symlink() or not path.is_file():
        die('missing or non-regular evidence file: ' + str(path))
    return file_hash(path)


def require_analysis_context(root, s, *, check_source=False, new_tests=()):
    require_preflight(s, root)
    rec = s.get('analysis_context', {})
    path = tdir(root, s['slug']) / 'analysis-context.json'
    if rec.get('status') != 'READY' or not path.is_file():
        die('evidence-based analysis context is required')
    if rec.get('artifact_hash') != regular_hash(path):
        die('analysis context artifact changed; restore the recorded analysis')
    if rec.get('preflight_fingerprint') != s['preflight']['fingerprint']:
        die('analysis context is stale for current preflight')
    if check_source:
        current = file_snapshot(root)
        current_fingerprint = fingerprint(root, current)
        if (s['size'] == 'large' and not s.get('frozen_tests') and
                rec.get('code_fingerprint') != s['start_fingerprint']):
            die('source changed before design approval; restore the task-start baseline and record analysis again')
        if rec.get('code_fingerprint') != current_fingerprint:
            previous = rec['source_snapshot']
            added = set(current) - previous.keys()
            if (not added or not added <= set(new_tests) or
                    fingerprint(root, {k: v for k, v in current.items() if k not in added}) != rec['code_fingerprint']):
                die('analysis context is stale for pre-implementation code; record analysis-context again')


def relative_file(root, value, where):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        die('expected repository-relative path in ' + where)
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        die('missing file or path outside repository in ' + where + ': ' + value)
    return path


def check_citations(root, citations):
    if not citations:
        die('at least one path:line | verified claim is required')
    for item in citations:
        match = re.fullmatch(r'(.+):([1-9][0-9]*)\s+\|\s+(\S.*)', item)
        if not match:
            die('analysis evidence must use path:line | verified claim: ' + item)
        path = relative_file(root, match[1], 'analysis evidence')
        number = int(match[2])
        with path.open('rb') as source:
            if not any(n == number for n, _ in enumerate(source, 1)):
                die('analysis evidence references a missing line: ' + item)


def gate_context_fingerprint(root, cfg, commands, manifest):
    cache = cfg.get('gate_cache', {})
    if not isinstance(cache, dict) or not isinstance(cache.get('enabled', False), bool):
        die('gate_cache must be an object with a boolean enabled flag')
    if not cache.get('enabled'):
        return None
    declarations = {}
    for group in ('dependency_files', 'config_files'):
        paths = cache.get(group)
        if not isinstance(paths, list) or not paths:
            return None
        if not all(isinstance(p, str) for p in paths):
            die('gate_cache.' + group + ' must contain paths')
        declarations[group] = paths
    env_names = cache.get('env_vars', [])
    if not isinstance(env_names, list) or not all(isinstance(x, str) and x for x in env_names):
        die('gate_cache.env_vars must be a list of names')
    probes = cache.get('environment')
    if not isinstance(probes, list) or not probes:
        return None
    observed = []
    for probe in probes:
        command = validate_command(probe, 'environment probe')
        executable = shutil.which(command[0])
        if not executable:
            return None
        status, _, digest = capture_command(root, command, 30)
        if status or digest is None:
            return None
        observed.append([command, str(Path(executable).resolve()), file_hash(Path(executable).resolve()), digest])
    declared = {group: {p: file_hash(relative_file(root, p, 'gate_cache.' + group)) for p in paths}
                for group, paths in declarations.items()}
    return json_hash([manifest, commands, declared, observed, platform.platform(), sys.version,
                      {name: os.environ.get(name) for name in env_names}])


def impact_checks(cfg, changed):
    components=cfg['components']
    direct=set()
    unknown=[]
    for path in changed:
        matches={name for name,entry in components.items()
                 if isinstance(entry,dict) and any(fnmatch.fnmatchcase(path,pattern) for pattern in entry.get('paths',[]))}
        if not matches:unknown.append(path)
        direct.update(matches)
    if unknown:die('unmapped changed paths; update .engineering-pipeline/checks.json before slice check: '+', '.join(unknown[:20]))
    # "depends_on" means consumer -> dependencies. If a dependency changes,
    # we also check every transitive consumer downstream.
    impacted=set(direct)
    while True:
        newly={name for name,entry in components.items()
               if isinstance(entry,dict) and set(entry.get('depends_on',[])) & impacted}
        if newly <= impacted:break
        impacted |= newly
    commands=[]
    for name in sorted(impacted):
        entry=components[name]
        if not isinstance(entry.get('slice_checks'),list) or not entry['slice_checks']:
            die('component '+name+' has no slice_checks; cannot silently skip coverage')
        for command in entry['slice_checks']:
            argv=tuple(validate_command(command,name))
            if argv not in commands:commands.append(argv)
    return sorted(impacted),commands

def require_frozen(root, s):
    require_analysis_context(root, s)
    if not s.get('frozen_tests'):
        die('no frozen acceptance tests; task freeze-tests is mandatory')
    for name, digest in s['frozen_tests'].items():
        path = relative_file(root, name, 'frozen test')
        if path != root / name or regular_hash(path) != digest:
            die('frozen acceptance test changed: ' + name)


def gate_evidence_matches(record, log, manifest, commands):
    return (record.get('id') and record.get('manifest') == manifest
            and record.get('commands') == commands and log.is_file() and not log.is_symlink()
            and record.get('log_hash') == file_hash(log))


def intact_slices(directory, state):
    for index in state.get('slice_chain', []):
        record = state['slice_checks'][index]
        if record.get('log') != f'slice-{index + 1}.log':
            return False
        log = directory / record['log']
        if record['status'] != 'PASS' or not log.is_file() or log.is_symlink() or record['log_hash'] != file_hash(log):
            return False
    return True


def require_gate(root, s):
    gate = s.get('full_gate', {})
    if gate.get('status') != 'PASS':
        die('full gate not green for current code fingerprint; run task gate')
    config, manifest = load_checks(root)
    commands = config['full_checks']
    if gate.get('cacheable'):
        current = gate_context_fingerprint(root, config, commands, manifest)
        if current != gate.get('context_fingerprint'):
            die('full gate validation context changed; run task gate again')
    # A configured environment probe is an external command. Inspect all live inputs after it exits.
    require_frozen(root, s)
    directory = tdir(root, s['slug'])
    if not intact_slices(directory, s):
        die('slice evidence changed; run task slice-check and gate again')
    if gate.get('fingerprint') != fingerprint(root):
        die('full gate not green for current code fingerprint; run task gate')
    if (not commands or manifest != manifest_identity(root)
            or not gate_evidence_matches(gate, directory / 'full-gate.log', manifest, commands)):
        die('full gate manifest or evidence log changed or missing; run task gate again')
    return gate


def review_input(root, s, stage, *, dispatch=False):
    policy = s['routing']
    common = [s['task_id'], s['preflight'].get('fingerprint'), s['analysis_context'].get('artifact_hash'), policy]
    if stage == 'design':
        if s['size'] != 'large':
            die('small tasks have no design review')
        require_analysis_context(root, s, check_source=dispatch and not s.get('frozen_tests'))
        baseline = s.get('design_baseline') or s['analysis_context']['code_fingerprint']
        design = tdir(root, s['slug']) / 'design.md'
        if not design.is_file() or not design.read_text(encoding='utf8').strip():
            die('large task requires non-empty design.md')
        subject = json_hash(common + [s['architect_model'], regular_hash(design), baseline])
        evidence = None
    else:
        gate = require_gate(root, s)
        require_design(root, s)
        subject = json_hash(common + [s['implementer_model'], gate['fingerprint'], gate['manifest'], gate['context_fingerprint'],
                                      s['frozen_tests'], s.get('design_approved_fingerprint')])
        evidence = [gate['id'], gate['context_fingerprint'], gate['log_hash']]
    if subject == s.get('rejected_subject', {}).get(stage):
        die(stage + ' review returned FIX; update the reviewed inputs before requesting new reviews')
    token = json_hash([stage, subject, evidence, s['review_generation'][stage]])
    if (stage == 'design' and dispatch and s.get('frozen_tests') and s.get('design_approved_fingerprint') != token
            and baseline != fingerprint(root)):
        die('design baseline changed; run task reopen-design before dispatch')
    return {'against': token, 'subject': subject}


def require_pair(s, stage, against):
    reviews = s[stage + '_reviews']
    author = s['architect_model' if stage == 'design' else 'implementer_model']
    identities = [author]
    for focus in 'ab':
        role = f'pipeline-{stage}-{focus}'
        review = reviews.get(focus, {})
        if (review.get('against') != against or review.get('role') != role or review.get('verdict') != 'PASS'
                or review.get('blockers') != 0 or not review.get('checked')):
            die('missing/failing/stale ' + stage + ' review ' + focus)
        validate_report(review, against)
        identities.append(review.get('model'))
    if reviews['a'].get('session') and reviews['a']['session'] == reviews['b'].get('session'):
        die(stage + ' reviewers shared session')
    routing.validate_review_models(s['routing'], identities)


def require_design(root, s):
    if s['size'] != 'large':
        return
    current = review_input(root, s, 'design')['against']
    require_pair(s, 'design', current)
    if s.get('design_approved_fingerprint') != current:
        die('human design approval missing/stale; run task approve-design')


def invalidate(s, stage):
    s[stage + '_reviews'] = {}
    s['review_generation'][stage] += 1
    if stage == 'design':
        s['design_approved_fingerprint'] = None
        s['design_approved_at'] = None
        invalidate(s, 'code')


def record_review(root, s, a, stage):
    current = review_input(root, s, stage, dispatch=True)
    if a.against != current['against']:
        die('stale review input; request a fresh review against the current version')
    try:
        findings = json.loads(a.findings) if a.findings.strip() else []
    except ValueError:
        die('--findings must be a JSON list of structured findings; use --checked for inspected evidence')
    report = validate_report({'against': a.against, 'verdict': a.verdict, 'blockers': a.blockers,
                              'findings': findings, 'checked': a.checked}, current['against'])
    role = f'pipeline-{stage}-{a.focus}'
    model = getattr(a, 'model', None) or s['routing']['models'].get(role)
    if model:
        routing.validate_selector(s['host'], model)
    record = {**report, 'role': role, 'model': model, 'session': a.session, 'recorded_at': now()}
    old = s[stage + '_reviews'].get(a.focus, {})
    if old.get('against') == a.against and a.verdict == 'PASS':
        if all(old.get(k) == v for k, v in record.items() if k != 'recorded_at'):
            print('Review already recorded', a.focus, a.against)
            return
        die('review already recorded for this input and focus; send FIX to reopen an incorrect result')
    if a.verdict == 'FIX':
        invalidate(s, stage)
        s.setdefault('rejected_subject', {})[stage] = current['subject']
    s[stage + '_reviews'][a.focus] = record
    save_state(root, s['slug'], s)
    print('Saved', stage, 'review', a.focus, a.verdict, a.against)


def ensure_private_tasks(root):
    exclusion = root / os.fsdecode(git(root, 'rev-parse', '--git-path', 'info/exclude')).strip()
    with file_lock(exclusion.with_name('engpipe-init.lock')):
        lines = exclusion.read_text(encoding='utf8', errors='surrogateescape').splitlines() if exclusion.exists() else []
        managed = ['/.engineering-pipeline/*', '!/.engineering-pipeline/checks.json']
        if not all(line in lines for line in managed) or '.engineering-pipeline/' in lines:
            lines = [line for line in lines if line != '.engineering-pipeline/' and line not in managed]
            atomic_text(exclusion, '\n'.join(lines + managed) + '\n')
    if git(root, 'ls-files', '--', '.engineering-pipeline/tasks'):
        die('task directory is tracked by Git; remove private task files from the index before proceeding')
    check = subprocess.run(['git', 'check-ignore', '-q', '--', '.engineering-pipeline/tasks/private-state'],
                           cwd=root)
    if check.returncode:
        die('project Git rules expose private task files; ignore /.engineering-pipeline/tasks/ before proceeding')


def cmd_init(a):
    root = project_root()
    ensure_private_tasks(root)
    directory = root / '.engineering-pipeline'
    directory.mkdir(exist_ok=True)
    checks = directory / 'checks.json'
    verify_private_paths(root, directory / 'tasks' / '.init.lock')
    with file_lock(directory / 'tasks' / '.init.lock'):
        if not checks.exists():
            atomic_json(checks, {'components': {}, 'full_checks': [], 'gate_cache': {'enabled': False}})
    print('Project initialized:', directory)
    print('Configure and commit .engineering-pipeline/checks.json. Task history stays local.')



def new_task(root, slug, kind, size, host, architect, architect_model=None, implementer_model=None):
    snap = file_snapshot(root)
    policy = routing.task_policy(host)
    for explicit in (architect_model, implementer_model):
        if explicit:
            routing.validate_selector(host, explicit)
    task_id = uuid.uuid4().hex
    base_commit = git(root, 'rev-parse', 'HEAD').decode().strip()
    baseline_tree = worktree_tree(root, tdir(root, slug), snap)
    if file_snapshot(root) != snap or git(root, 'rev-parse', 'HEAD').decode().strip() != base_commit:
        die('worktree changed while capturing the baseline; retry task start')
    return {'workflow_version': WORKFLOW_VERSION, 'task_id': task_id, 'base_commit': base_commit,
            'baseline_tree': baseline_tree, 'baseline_kind': 'task-start', 'start_fingerprint': json_hash([base_commit, snap]),
            'slug': slug, 'kind': kind,
            'size': size, 'created_at': now(), 'host': host, 'routing': policy,
            'architect_role': architect if size == 'large' else None, 'implementer_role': 'pipeline-implementer',
            'architect_model': (architect_model or policy['models'].get(architect)) if size == 'large' else None,
            'implementer_model': implementer_model or policy['models'].get('pipeline-implementer'),
            'status': 'PREFLIGHT', 'preflight': {}, 'analysis_context': {}, 'design_reviews': {}, 'code_reviews': {},
            'review_generation': {'design': 0, 'code': 0}, 'frozen_tests': {}, 'full_gate': {},
            'slice_checks': [], 'initial_snapshot': snap, 'slice_snapshot': snap}


def migrate(root, old, a):
    version = old.get('workflow_version', 1)
    if version not in (1, 2):
        die('only legacy version 1/2 in-flight tasks need migration')
    slug = old['slug']
    td = tdir(root, slug)
    # Frozen evidence remains binding across migration; never re-baseline modified tests.
    frozen = {}
    for name, digest in old.get('frozen_tests', {}).items():
        normalized = name.replace('\\', '/')
        path = relative_file(root, normalized, 'legacy frozen test')
        if regular_hash(path) != digest:
            die('restore the original frozen test before migration: ' + name)
        frozen[path.relative_to(root).as_posix()] = digest
    state = new_task(root, slug, old['kind'], old['size'], a.host or 'opencode', old.get('architect_role') or ARCHS[1],
                     old.get('architect_model') if a.host is None else None,
                     old.get('implementer_model') if a.host is None else None)
    state['migration_frozen_tests'] = frozen
    state['migrated_from'] = version
    # A legacy slice snapshot is a moving checkpoint and cannot reconstruct the original task diff.
    # A diff from the empty tree gives reviewers all current source, including previously committed work.
    state['baseline_kind'] = 'full-repository-after-migration'
    state['baseline_tree'] = worktree_tree(root, td, {})
    known = {name.replace('\\', '/') for name in old.get('slice_snapshot', {})}
    for record in old.get('slice_checks', []):
        known.update(name.replace('\\', '/') for name in record.get('changed', []))
    current = state['initial_snapshot']
    state['migration_missing_paths'] = sorted(name for name in known if name not in current or current[name].endswith(':missing'))
    if a.host is None:
        state['routing'] = routing.legacy_routing()
        for role in ('architect', 'implementer'):
            recorded = old.get(role + '_model')
            assigned = state.get(role + '_role')
            if assigned:
                state[role + '_model'] = recorded or state['routing']['models'][assigned]
    # Old snapshots lack types, modes and index information. Recheck every existing and previously known path.
    baseline = {name: 'legacy-unverified' for name in known}
    state['initial_snapshot'] = state['slice_snapshot'] = baseline
    archive = td / f'legacy-v{version}'
    files = [p for p in td.iterdir() if (p.is_symlink() or p.is_file()) and p.name not in ('.lock', 'runner.lock')]
    verify_private_paths(root, archive, *files, *(archive / p.name for p in files))
    archive.mkdir(exist_ok=True)
    for path in files:
        if not (archive / path.name).exists():
            shutil.copy2(path, archive / path.name)
    save_state(root, slug, state)
    print(f'Saved prior evidence in {archive}. Record preflight, analysis, required design approval, freeze-tests, '
          'slice-check, gate and both reviews. Prior PASS records do not carry forward.')


def cmd_task(a):
    root = project_root()
    directory = tdir(root, a.slug)
    if directory.resolve() != directory:
        die('task state directories must reside in the repository without symlinks')
    if a.op == 'start':
        ensure_private_tasks(root)
    verify_private_paths(root, directory, directory.parent / '.runner-intent.json',
                         *(directory / name for name in ('.lock', 'state.json', 'quarantine.json', 'preflight.json',
                                                         'analysis-context.json', 'design.md', 'full-gate.log')))
    if a.op != 'start' and not (directory / 'state.json').is_file():
        die('task does not exist: ' + a.slug)
    with file_lock(directory / '.lock'):
        return locked_task(root, a)


def locked_task(root, a):
    path = task_json(root, a.slug)
    td = path.parent
    if a.op == 'start':
        if path.exists():
            die('task already exists: ' + a.slug)
        state = new_task(root, a.slug, a.kind, a.size, getattr(a, 'host', 'opencode'), a.architect,
                         getattr(a, 'architect_model', None), getattr(a, 'implementer_model', None))
        save_state(root, a.slug, state)
        print('Task created:', td)
        return
    s = get_state(root, a.slug)
    quarantine = td / 'quarantine.json'
    intent_path = root / '.engineering-pipeline/tasks/.runner-intent.json'
    unsafe = False
    if intent_path.exists():
        intent = read_json(intent_path)
        if intent.get('slug') == a.slug:
            from runner import guard_violation
            unsafe = guard_violation(intent['guard'], s)
            if not unsafe and a.op in ('approve-design', 'code-review', 'design-review', 'finish'):
                die('runner host call in progress; protected decisions are owned by the runner')
    if quarantine.exists() or quarantine.is_symlink() or unsafe:
        if a.op == 'status':
            s['previous_status'] = s['status']
            s['status'] = 'QUARANTINED'
            print(json.dumps({'task': s, 'quarantine': quarantine.name}, ensure_ascii=False))
        die('task quarantined; inspect its artifacts and worktree, then start a new task')
    if a.op == 'status':
        print(json.dumps({'task': s, 'current_code_fingerprint': fingerprint(root)}, indent=2, ensure_ascii=False))
        return
    if s['status'] == 'DONE':
        die('task already DONE')
    if a.op == 'migrate':
        return migrate(root, s, a)
    if s.get('workflow_version', 1) != WORKFLOW_VERSION:
        die('incompatible task version; for v1/v2 run engpipe task migrate ' + a.slug + ' --host <host>')

    if a.op in ('preflight', 'analysis-context'):
        if s['frozen_tests'] or s['slice_checks'] or s['full_gate']:
            die('cannot replace requirements after implementation has started')
        if a.op == 'preflight':
            if not a.goal.strip() or not all(x.strip() for x in a.in_scope + a.acceptance):
                die('preflight goal, scope and acceptance must be nonempty')
            data = {'goal': a.goal, 'in_scope': a.in_scope, 'out_of_scope': a.out_of_scope,
                    'constraints': a.constraint, 'acceptance': a.acceptance, 'decisions': a.decision,
                    'blocking_questions': a.blocking_question}
            artifact = td / 'preflight.json'
            atomic_json(artifact, data)
            ready = not a.blocking_question
            s['preflight'] = {'status': 'READY' if ready else 'BLOCKED', 'fingerprint': regular_hash(artifact),
                              'artifact': artifact.name, 'recorded_at': now()}
            s['analysis_context'] = {}
            s['status'] = 'ANALYSIS' if ready else 'PREFLIGHT_BLOCKED'
        else:
            require_preflight(s, root)
            check_citations(root, a.evidence)
            data = {'preflight_fingerprint': s['preflight']['fingerprint'], 'evidence': a.evidence,
                    'components': a.component, 'tests': a.test, 'risks': a.risk, 'decisions': a.decision,
                    'unknowns': a.unknown}
            artifact = td / 'analysis-context.json'
            snapshot = file_snapshot(root)
            source = fingerprint(root, snapshot)
            if s['size'] == 'large' and source != s['start_fingerprint']:
                die('source changed before design approval; restore the task-start baseline before analysis')
            atomic_json(artifact, data)
            s['analysis_context'] = {'status': 'READY', 'artifact': artifact.name, 'artifact_hash': regular_hash(artifact),
                                     'preflight_fingerprint': s['preflight']['fingerprint'], 'source_snapshot': snapshot,
                                     'code_fingerprint': source, 'recorded_at': now()}
            s['status'] = 'DESIGN' if s['size'] == 'large' else 'IMPLEMENTATION_READY'
        invalidate(s, 'design')
        save_state(root, a.slug, s)
        print(s['status'], artifact)
        return
    if a.op == 'reopen-design' or a.op == 'author-model':
        stage = 'design' if a.op == 'reopen-design' or a.role == 'architect' else 'code'
        if stage == 'design':
            if s['size'] != 'large':
                die('no design assigned to small task')
            if s['frozen_tests']:
                require_frozen(root, s)
                s['design_baseline'] = fingerprint(root)
        if a.op == 'author-model':
            routing.validate_selector(s['host'], a.model)
            s[a.role + '_model'] = a.model
        invalidate(s, stage)
        save_state(root, a.slug, s)
        print('Repeat the affected reviews and required approval')
        return
    if a.op == 'freeze-tests':
        expected = {}
        for name in a.paths:
            path = relative_file(root, name, 'freeze-tests')
            relative = path.relative_to(root).as_posix()
            if root / name != path:
                die('frozen tests must use their real repository-relative path: ' + name)
            expected[relative] = regular_hash(path)
        if not expected:
            die('specify acceptance test paths; freeze before implementation')
        if s['frozen_tests']:
            require_frozen(root, s)
            if expected != s['frozen_tests']:
                die('frozen acceptance set is immutable; start a new task to change it')
            print('Frozen acceptance tests unchanged')
            return
        for name, digest in s.get('migration_frozen_tests', {}).items():
            if expected.get(name) != digest:
                die('migration must retain legacy frozen test: ' + name)
        require_analysis_context(root, s, check_source=True, new_tests=expected.keys())
        require_design(root, s)
        s['frozen_tests'] = expected
        s['status'] = 'IMPLEMENTATION'
        save_state(root, a.slug, s)
        print('Frozen', len(expected), 'acceptance test(s)')
        return
    if a.op == 'review-input':
        inputs = review_input(root, s, a.stage, dispatch=True)
        print(json.dumps({'against': inputs['against']}))
        return
    if a.op in ('design-review', 'code-review'):
        return record_review(root, s, a, a.op.split('-')[0])
    if a.op == 'approve-design':
        if not sys.stdin.isatty():
            die('approval requires an interactive terminal')
        current = review_input(root, s, 'design', dispatch=True)['against']
        require_pair(s, 'design', current)
        expected = 'APPROVE ' + a.slug
        print(f'Design {td / "design.md"} has two PASS reviews against {current}.')
        if input('Type exactly ' + expected + ': ') != expected:
            die('approval not granted')
        if review_input(root, s, 'design', dispatch=True)['against'] != current:
            die('design changed during approval')
        s['design_approved_fingerprint'] = current
        s['design_approved_at'] = now()
        save_state(root, a.slug, s)
        print('Design approval recorded')
        return
    if a.op == 'slice-check':
        require_frozen(root, s)
        require_design(root, s)
        cfg, manifest = load_checks(root)
        before = s['slice_snapshot']
        # A revised impact map must cover the whole outstanding task again.
        impact_id = json_hash(cfg['components'])
        reset_chain = (s.get('slice_manifest') not in (None, impact_id) or not intact_slices(td, s))
        if reset_chain:
            before = s['initial_snapshot']
        current = file_snapshot(root)
        changed = changed_files_since(before, current)
        if not changed:
            if not reset_chain:
                die('no changed files since prior slice check')
            # Reverting the entire task removes pending impacts, but the revert itself needs evidence.
            changed = changed_files_since(s['slice_snapshot'], current)
        impacted, commands = impact_checks(cfg, changed)
        fp_before = fingerprint(root, current)
        log = td / ('slice-' + str(len(s['slice_checks']) + 1) + '.log')
        verify_private_paths(root, log)
        ok = run_checks(root, commands, log)
        after = fingerprint(root)
        ok = ok and fp_before == after and manifest_identity(root) == manifest
        record = {'status': 'PASS' if ok else 'FAIL', 'changed': changed, 'components': impacted,
                  'commands': [list(x) for x in commands], 'fingerprint': after, 'log': log.name,
                  'manifest': manifest, 'log_hash': regular_hash(log), 'at': now()}
        s['slice_checks'].append(record)
        if ok:
            s['slice_snapshot'] = current
            s['slice_manifest'] = impact_id
            s['slice_chain'] = ([] if reset_chain else s.get('slice_chain', [])) + [len(s['slice_checks']) - 1]
        s['full_gate'] = {}
        invalidate(s, 'code')
        save_state(root, a.slug, s)
        if not ok:
            die('targeted slice checks FAIL / inputs changed during checks; see ' + str(log))
        print('Slice checks PASS:', ', '.join(impacted), 'files:', len(changed))
        return
    if a.op == 'gate':
        require_frozen(root, s)
        require_design(root, s)
        cfg, manifest = load_checks(root)
        commands = cfg['full_checks']
        if not commands:
            die('no configured full_checks in .engineering-pipeline/checks.json')
        context_before = gate_context_fingerprint(root, cfg, commands, manifest)
        require_frozen(root, s)
        require_design(root, s)
        snapshot = file_snapshot(root)
        if s['slice_snapshot'] != snapshot or not intact_slices(td, s) or s.get('slice_manifest') not in (None, json_hash(cfg['components'])):
            die('unverified changes; run task slice-check before the full gate')
        if manifest != manifest_identity(root):
            die('checks manifest changed during the environment probe; run task gate again')
        before = fingerprint(root, snapshot)
        log = td / 'full-gate.log'
        previous = s['full_gate']
        if (context_before and previous.get('status') == 'PASS' and previous.get('fingerprint') == before
                and previous.get('context_fingerprint') == context_before
                and gate_evidence_matches(previous, log, manifest, commands)):
            previous['reuse_count'] += 1
            previous['last_reused_at'] = now()
            save_state(root, a.slug, s)
            print('Full gate REUSED on', before)
            return
        # Persist invalidation first. Interrupted retries cannot resurrect an earlier PASS.
        invalidate(s, 'code')
        s['full_gate'] = {'status': 'RUNNING'}
        save_state(root, a.slug, s)
        success = run_checks(root, commands, log)
        context_after = gate_context_fingerprint(root, cfg, commands, manifest)
        after = fingerprint(root)
        final_manifest = manifest_identity(root)
        require_frozen(root, s)
        require_design(root, s)
        success = success and before == after and manifest == final_manifest and context_before == context_after
        s['full_gate'] = {'id': uuid.uuid4().hex, 'status': 'PASS' if success else 'FAIL',
                          'fingerprint': after, 'context_fingerprint': context_after,
                          'cacheable': bool(success and context_after), 'at': now(), 'log': log.name,
                          'manifest': manifest, 'commands': commands, 'log_hash': regular_hash(log), 'reuse_count': 0}
        save_state(root, a.slug, s)
        if not success:
            die('full gate FAIL or inputs modified during gate; see ' + str(log))
        print('Full gate PASS on', after)
        return
    if a.op == 'finish':
        current = review_input(root, s, 'code')['against']
        require_pair(s, 'code', current)
        s['session_audit'] = {
            'design_ids_present': s['size'] != 'large' or all(s['design_reviews'][x].get('session') for x in 'ab'),
            'code_ids_present': all(s['code_reviews'][x].get('session') for x in 'ab')}
        s['status'] = 'DONE'
        s['finished_at'] = now()
        save_state(root, a.slug, s)
        print('DONE: current gate and two matching reviews. Host identities and isolation are operator-reported.')
        return
    die('unknown task action')


def main():
    # Machine-readable responses and diagnostics have the same encoding on all hosts.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    p=argparse.ArgumentParser(description='Portable engineering agents and deterministic gates')
    s=p.add_subparsers(dest='command',required=True)
    from runner import add_runner_command
    add_runner_command(s, sys.modules[__name__])
    host_config.add_host_commands(s)
    x=s.add_parser('init');x.set_defaults(fn=cmd_init)
    x=s.add_parser('task');xs=x.add_subparsers(dest='op',required=True)
    for op in ('start','status','migrate','reopen-design','review-input','preflight','analysis-context','author-model','freeze-tests','design-review','approve-design','gate','slice-check','code-review','finish'):
        z=xs.add_parser(op);z.add_argument('slug')
        if op in ('start', 'migrate'):
            z.add_argument('--host', choices=['opencode','claude','codex'], default='opencode' if op == 'start' else None)
        if op == 'review-input':
            z.add_argument('stage', choices=['design','code'])
        if op=='start':z.add_argument('--kind',choices=['feature','bugfix','refactor'],required=True);z.add_argument('--size',choices=['small','large'],required=True);z.add_argument('--architect',choices=ARCHS,default='pipeline-architect-l2');z.add_argument('--architect-model');z.add_argument('--implementer-model')
        if op=='preflight':
            z.add_argument('--goal',required=True);z.add_argument('--in-scope',action='append',required=True)
            z.add_argument('--out-of-scope',action='append',default=[]);z.add_argument('--constraint',action='append',default=[])
            z.add_argument('--acceptance',action='append',required=True);z.add_argument('--decision',action='append',default=[])
            z.add_argument('--blocking-question',action='append',default=[])
        if op=='analysis-context':
            z.add_argument('--evidence',action='append',required=True);z.add_argument('--component',action='append',default=[])
            z.add_argument('--test',action='append',default=[]);z.add_argument('--risk',action='append',default=[])
            z.add_argument('--decision',action='append',default=[]);z.add_argument('--unknown',action='append',default=[])
        if op=='freeze-tests':z.add_argument('paths',nargs='+')
        if op=='author-model':z.add_argument('--role',choices=['architect','implementer'],required=True);z.add_argument('--model',required=True)
        if op in ('design-review','code-review'):
            z.add_argument('--against', required=True, help='Input token issued before dispatch via task review-input')
            z.add_argument('--checked', action='append', required=True, help='Specific contract, file or evidence examined')
            z.add_argument('focus',choices=['a','b']);z.add_argument('verdict',choices=['PASS','FIX']);z.add_argument('blockers',type=int);z.add_argument('--session',default=None,help='Optional actual child session ID; never invent one');z.add_argument('--model',help='Reviewer model observed in this host');z.add_argument('--findings',default='',help='JSON array of severity, location, evidence, impact, suggested_fix objects')
        z.set_defaults(fn=cmd_task)
    a=p.parse_args()
    try:a.fn(a)
    except (PipelineError,ValueError,KeyError,OSError) as e:
        print('PIPELINE ERROR:',str(e),file=sys.stderr);sys.exit(2)

if __name__=='__main__':main()
