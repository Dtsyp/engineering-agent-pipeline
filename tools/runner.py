"""Console driver for the same task commands and host roles used interactively."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import host_config
from host_process import communicate, executable_argv
import routing
from storage import atomic_text, file_lock, verify_private_paths
from review_contract import validate_report

CLI = Path(__file__).with_name('pipeline.py').resolve()
ORCHESTRATOR = 'pipeline-orchestrator'


def save(root, path, value):
    verify_private_paths(root, path)
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def task(root, *arguments, optional=False):
    result = subprocess.run([sys.executable, str(CLI), 'task', *arguments], cwd=root,
                            capture_output=True, encoding='utf-8', errors='replace',
                            stdin=subprocess.DEVNULL)
    if result.returncode and not optional:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'task command failed')
    return result


def review_input(root, slug, stage):
    result = task(root, 'review-input', slug, stage, optional=True)
    if result.returncode:
        return None, result.stderr.strip()
    value = json.loads(result.stdout.strip().splitlines()[-1])
    return value['against'], None


def host_reply(host, stream):
    """Extract assistant output and provider-issued session IDs, never tool output."""
    session = None
    reply = None
    phase = "initial"
    for line in iter(lambda: stream.readline(2 * 1024 * 1024 + 1), b''):
        if len(line) > 2 * 1024 * 1024:
            raise ValueError(f'{host} returned an oversized protocol record')
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f'{host} returned a malformed event')
        kind = event.get('type')
        if phase == 'complete':
            raise ValueError(f'{host} returned another event after successful completion')
        if host == 'claude':
            if kind != 'result' or event.get('is_error') or event.get('subtype') != 'success':
                raise ValueError('Claude print mode returned an unsuccessful result')
            reply = event.get('result')
            session = event.get('session_id')
            phase = 'complete'
        elif host == 'codex':
            if kind == 'thread.started' and phase == 'initial':
                session = event.get('thread_id')
                if not isinstance(session, str) or not session.strip():
                    raise ValueError('Codex started a thread without an ID')
                phase = 'thread'
            elif kind == 'turn.started' and phase == 'thread':
                phase = 'active'
            elif kind in {'item.started', 'item.updated', 'item.completed'} and phase == 'active':
                item = event.get('item')
                if not isinstance(item, dict):
                    raise ValueError('Codex returned an invalid item')
                if kind == 'item.completed' and item.get('type') == 'agent_message':
                    reply = item.get('text')
            elif kind == 'turn.completed' and phase == 'active':
                phase = 'complete'
            else:
                raise ValueError(f'failed, interleaved or unrecognized Codex event: {kind}')
            if event.get('thread_id', session) != session:
                raise ValueError('Codex mixed multiple threads in one reply')
        else:
            if kind == 'error':
                raise ValueError('OpenCode reported an unsuccessful turn')
            if kind == 'text' and isinstance(event.get('part'), dict):
                reply = event['part'].get('text')
                session = event.get('sessionID')
    if host == 'codex' and phase != 'complete':
        raise ValueError('Codex stream ended before turn.completed')
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError(f'{host} did not return a final assistant message')
    if session is not None and (not isinstance(session, str) or not session or len(session) > 512):
        raise ValueError(f'{host} returned a malformed session ID')
    return reply, session


def codex_review_options(executable, root):
    options = []
    for feature in ('apps', 'plugins', 'hooks', 'request_permissions_tool', 'multi_agent'):
        options.extend(['--disable', feature])
    result = subprocess.run([*executable, *options, 'mcp', 'list', '--json'], cwd=root,
                            capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError('cannot inspect Codex MCP configuration for read-only review')
    servers = json.loads(result.stdout)
    if not isinstance(servers, list):
        raise ValueError('unrecognized Codex MCP catalog')
    for server in servers:
        name = server.get('name') if isinstance(server, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError('unrecognized Codex MCP server')
        options.extend(['-c', 'mcp_servers.' + json.dumps(name) + '.enabled=false'])
    return options


def invoke(host, role, prompt, root, directory, timeout, model):
    executable = executable_argv(host)
    readonly = role != ORCHESTRATOR
    model_args = ['--model', model] if model else []
    request = None
    if host == 'codex':
        mode = 'read-only' if readonly else 'workspace-write'
        options = codex_review_options(executable, root) if readonly else []
        cmd = [*executable, *options, 'exec', '--sandbox', mode, '--json', *model_args, '-']
        # Codex exec has no flag for starting a custom role as the main thread.
        prompt = host_config.agent_parts(role)[1] + '\n\n' + prompt
    elif host == 'claude':
        cmd = [*executable, '-p', '--output-format', 'json', '--agent', role, *model_args]
        if readonly:
            cmd.extend(['--tools', 'Read,Glob,Grep', '--disallowedTools', 'mcp__*'])
    else:
        verify_private_paths(root, directory)
        # A fixed message and relative attachment keep user text off argv even
        # on Windows, where an npm-installed CLI may be a command shim.
        with tempfile.NamedTemporaryFile('w', suffix='.md', prefix='request-', dir=directory,
                                         encoding='utf8', delete=False) as output:
            request = Path(output.name)
            output.write(prompt)
        cmd = [*executable, 'run', 'Follow the attached task instructions.', '--agent', role,
               '--format', 'json', *model_args, '--file', str(request.relative_to(root))]
    print(f'Dispatching {role} via {host}', flush=True)
    try:
        with tempfile.TemporaryFile('w+b') as transcript:
            exit_code = communicate(cmd, cwd=root, stdin_bytes=b'' if request else prompt.encode('utf8'),
                                    stdout=transcript, timeout=timeout)
            if exit_code:
                raise ValueError(f'{host} {role} exited {exit_code}; inspect the host session or run engpipe doctor --target {host}')
            transcript.seek(0)
            return host_reply(host, transcript)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'{host} {role} timed out; the task can be resumed') from exc
    finally:
        if request:
            request.unlink(missing_ok=True)


def validated_report(answer, token):
    return validate_report(json.loads(answer), token)


def write_diff(root, path, *arguments, env=None):
    verify_private_paths(root, path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            result = subprocess.run(['git', 'diff', '--no-ext-diff', '--no-textconv', '--binary', *arguments, '--'],
                                    cwd=root, stdout=output, stderr=subprocess.PIPE, env=env)
            if result.returncode:
                raise ValueError(result.stderr.decode('utf8', errors='replace').strip())
            output.close()
            os.replace(temporary, path)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)


def review_packet(root, directory, state, stage, token, original):
    from validation import tree_environment, worktree_tree

    prefix = f'{stage}-{token}'
    verify_private_paths(root, directory / 'objects', directory / 'preflight.json', directory / 'analysis-context.json',
                         directory / 'design.md', directory / 'full-gate.log')
    packet = {key: state.get(key) for key in ('slug', 'task_id', 'kind', 'size', 'base_commit',
              'baseline_tree', 'baseline_kind', 'migration_missing_paths', 'routing', 'architect_model', 'implementer_model', 'frozen_tests',
              'design_approved_fingerprint')}
    packet.update(against=token, request=original, stage=stage)
    if state.get('baseline_kind') == 'full-repository-after-migration':
        packet['coverage'] = ('Review the entire current source tree. The original task baseline is unknown. '
                              'Inspect migration_missing_paths and archived task evidence for historical deletions; '
                              'report any unverified deleted behavior as uncertainty.')
    for name in ('preflight', 'analysis-context'):
        packet[name] = json.loads((directory / (name + '.json')).read_text(encoding='utf8'))
    design = directory / 'design.md'
    if state['size'] == 'large':
        packet['design'] = design.read_text(encoding='utf8')
    if stage == 'code':
        tree = worktree_tree(root, directory)
        patch = directory / (prefix + '.patch')
        write_diff(root, patch, state['baseline_tree'], tree, env=tree_environment(directory))
        index = directory / (prefix + '.index.patch')
        write_diff(root, index, '--cached', 'HEAD')
        packet.update(reviewed_tree=tree, worktree_patch=patch.relative_to(root).as_posix(),
                      index_vs_head_patch=index.relative_to(root).as_posix(), full_gate=state['full_gate'],
                      manifest_identity=state['full_gate']['manifest'],
                      checks=json.loads((root / '.engineering-pipeline' / 'checks.json').read_text(encoding='utf8')),
                      gate_log=(directory / 'full-gate.log').relative_to(root).as_posix())
    if review_input(root, state['slug'], stage)[0] != token:
        raise ValueError('inputs changed while preparing review packet; resume for a new pair of reviews')
    path = directory / (prefix + '.json')
    save(root, path, packet)
    return path.relative_to(root).as_posix()


def collect_reviews(root, directory, request, stage, token, models, timeout, packet, missing, existing):
    slug, host = request['slug'], request['host']
    roles = [f'pipeline-{stage}-{focus}' for focus in missing]
    def review(role):
        prompt = (f'Role: {role}. Review task {slug}, stage {stage}, against {token}.\n'
                  f'Read the complete shared inputs at {packet}, including its Git patches, evidence log and '
                  'relevant sources. Do not inspect runner.json, state.json, peer reviews, histories or transcripts. '
                  'Use the role review contract. Return ONE JSON object containing against (the exact input ID), '
                  'verdict, blockers, findings (structured objects) and checked (specific evidence).')
        answer, session = invoke(host, role, prompt, root, directory, timeout, models.get(role))
        return {'report': validated_report(answer, token), 'session': session}
    # Do not publish either review until both independent processes have finished.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(zip(missing, pool.map(review, roles)))
    first, second = [(results.get(focus) or existing[focus]).get('session') for focus in 'ab']
    if first and first == second:
        raise ValueError('host reused a session for both reviewers')
    return results


def record_batch(core, slug, stage, token, batch):
    """Record PASS before FIX, since the first FIX closes the generation."""
    for focus in sorted(batch, key=lambda key: batch[key]['report']['verdict'] == 'FIX'):
        item = batch[focus]
        report = item['report']
        # Same command handler and lock as the public CLI, without Windows argv size or quoting limits.
        core.cmd_task(argparse.Namespace(op=stage + '-review', slug=slug, focus=focus,
                                        verdict=report['verdict'], blockers=report['blockers'],
                                        against=token, checked=report['checked'], session=item['session'],
                                        findings=json.dumps(report['findings'], ensure_ascii=False)))
        if report['verdict'] == 'FIX':
            return False
    return True


def protected_fields(state):
    return {key: state.get(key) for key in ('design_reviews', 'code_reviews',
            'design_approved_fingerprint', 'design_approved_at', 'status',
            'routing', 'slug', 'kind', 'size', 'base_commit', 'baseline_tree',
            'task_id', 'start_fingerprint', 'baseline_kind', 'migration_missing_paths')}


def guard_violation(previous, state):
    """Core status and recovery both consult the guard written before dispatch."""
    for field in ('design_reviews', 'code_reviews'):
        for focus, record in state.get(field, {}).items():
            if record != (previous.get(field) or {}).get(focus):
                return f'coordinator wrote {field}'
    for field in ('design_approved_fingerprint', 'design_approved_at'):
        if state.get(field) and state[field] != previous.get(field):
            return 'coordinator changed design approval'
    if state['status'] == 'DONE':
        return 'coordinator certified completion'
    if any(state.get(key) != previous.get(key) for key in
           ('routing', 'slug', 'kind', 'size', 'base_commit', 'baseline_tree',
            'task_id', 'start_fingerprint', 'baseline_kind', 'migration_missing_paths')):
        return 'coordinator changed task identity or policy'
    return None


def verify_intent(root, intent, core):
    issue = guard_violation(intent['guard'], core.get_state(root, intent['slug']))
    if issue:
        path = core.tdir(root, intent['slug']) / 'quarantine.json'
        save(root, path, {'attempt': intent['attempt'], 'reason': issue})
        return issue
    return None


def pending(root, control, args, core):
    if not control.exists():
        if args.recover_attempt:
            raise ValueError('no unresolved host attempt in this worktree')
        return False
    intent = json.loads(control.read_text(encoding='utf8'))
    issue = verify_intent(root, intent, core)
    slug, attempt = intent['slug'], intent['attempt']
    if args.recover_attempt:
        if args.slug != slug or args.recover_attempt != attempt:
            raise ValueError('attempt ID or task does not match the unresolved dispatch')
        control.unlink()
        if issue:
            print(f'Task {slug} remains quarantined: {issue}. Inspect changes and start a fresh task.')
        else:
            print(f'Reconciled attempt {attempt}. To continue, run engpipe run {slug} --resume')
        return True
    raise ValueError(f'worktree has an uncertain writer for {slug}, attempt {attempt}. '
                     f'Stop any remaining host work, inspect Git changes and task evidence, then acknowledge '
                     f'with engpipe run {slug} --recover-attempt {attempt}')


def coordinate(root, directory, request, stage, models, timeout, reason, core):
    slug = request['slug']
    phase = ('Prepare the design for independent review. Stop before either design review or implementation.'
             if stage == 'design' else
             'Implement through the real full gate, using an implementer per slice. Stop before either code review.')
    summary = request.get('repair') or {}
    prompt = (f'Console coordinator for existing task {slug}. Active stage: {stage}.\n'
              f'User request:\n{request["prompt"]}\n{phase}\n'
              'Read durable task state and fulfill only outstanding prerequisites with engpipe. '
              'Perform real preflight and evidence-based analysis, freeze agreed tests before implementation, '
              'then perform targeted validation for each change. If the project has no real checks, stop and '
              'explain what the operator needs to configure. If blocked, preserve progress and report the reason. '
              'The console driver dispatches BOTH reviewers and owns review registration, approval and finish. '
              'Do not call those commands or alter their records. Do not run engpipe run recursively.\n'
              f'Current readiness: {reason}\nPrevious review feedback: {json.dumps(summary, ensure_ascii=False)}')
    intent = {'slug': slug, 'attempt': uuid.uuid4().hex, 'stage': stage,
              'guard': protected_fields(core.get_state(root, slug))}
    control = directory.parent / '.runner-intent.json'
    save(root, control, intent)
    print(f'Mutable host attempt {intent["attempt"]}', flush=True)
    try:
        answer, _ = invoke(request['host'], ORCHESTRATOR, prompt, root, directory,
                           timeout, models.get(ORCHESTRATOR))
    finally:
        issue = verify_intent(root, intent, core)
        if issue:
            raise ValueError(f'{issue}; task quarantined; reconcile attempt {intent["attempt"]}')
    # Only a successful host turn with unchanged authorization clears its intent.
    control.unlink()
    return answer


def advance(root, directory, request, args, core):
    slug, host = request['slug'], request['host']
    attempts = 0
    first = True
    for _ in range(2 * args.max_rounds + 4):
        state = core.get_state(root, slug)
        if state.get('workflow_version') != core.WORKFLOW_VERSION:
            raise ValueError(f'legacy task requires engpipe task migrate {slug} --host {host}')
        if (directory / 'quarantine.json').exists():
            raise ValueError('task quarantined; inspect the recorded reason and use a fresh task')
        if state['status'] == 'DONE':
            print(f'Task {slug} already completed.')
            return
        policy = state.get('routing') or routing.task_policy(host)
        if policy != routing.task_policy(host):
            raise ValueError('task routing differs from the current host configuration; restore its declared routing')
        models = policy['models']
        if first:
            first = False
            cfg, _manifest = core.load_checks(root)
            if not cfg.get('full_checks'):
                raise ValueError('configure .engineering-pipeline/checks.json with actual project checks before dispatch')
            host_config.verify_installed(host)
            if host == 'opencode':
                host_config.check_opencode_roles([ORCHESTRATOR, *routing.REVIEWERS], cwd=root)
        stage = 'code'
        if state['size'] == 'large':
            try:
                core.require_design(root, state)
            except core.PipelineError:
                stage = 'design'
        token, reason = review_input(root, slug, stage)
        if not token:
            coordinate(root, directory, request, stage, models, args.timeout, reason, core)
            token, reason = review_input(root, slug, stage)
            if not token:
                raise ValueError(f'{stage} is not ready: {reason or "unresolved FIX"}. Inspect local task artifacts and the host session.')
            state = core.get_state(root, slug)
        existing = state.get(stage + '_reviews', {})
        missing = []
        for focus in 'ab':
            previous = existing.get(focus, {})
            if previous.get('against') == token and previous.get('verdict') == 'PASS':
                validate_report(previous, token)
                if previous.get('role') != f'pipeline-{stage}-{focus}':
                    raise ValueError('recorded review has the wrong role')
            else:
                missing.append(focus)
        if missing:
            checkpoint = request.get('batch') or {}
            if checkpoint.get('against') != token or checkpoint.get('stage') != stage:
                checkpoint = {}
            if not all(focus in checkpoint.get('reports', {}) for focus in missing):
                packet = review_packet(root, directory, state, stage, token, request['prompt'])
                reports = collect_reviews(root, directory, request, stage, token, models, args.timeout,
                                          packet, missing, existing)
                # Include native evidence in a subsequent repair handoff only after
                # the newly dispatched reviewers have finished in isolation.
                for focus in existing.keys() - set(missing):
                    native = existing[focus]
                    reports[focus] = {'session': native.get('session'), 'report': {
                        key: native[key] for key in ('against', 'verdict', 'blockers', 'checked', 'findings')}}
                checkpoint = {'stage': stage, 'against': token, 'reports': reports}
                request['batch'] = checkpoint
                save(root, directory / 'runner.json', request)
            if any(item['report']['verdict'] == 'FIX' for item in checkpoint['reports'].values()):
                request['repair'] = checkpoint
                save(root, directory / 'runner.json', request)
            if not record_batch(core, slug, stage, token, {focus: checkpoint['reports'][focus] for focus in missing}):
                print(f'{stage} review requires corrections.', flush=True)
                if attempts >= args.max_rounds:
                    raise ValueError(f'review round limit reached; inspect feedback and run engpipe run {slug} --resume')
                attempts += 1
                continue
        request.pop('repair', None)
        request.pop('batch', None)
        save(root, directory / 'runner.json', request)
        if stage == 'design':
            outcome = 3
            if core.interactive_input():
                outcome = subprocess.run(
                    [sys.executable, str(CLI), 'task', 'approve-design', slug], cwd=root).returncode
            if outcome == 3:
                print(f'PAUSED: inspect design.md, run engpipe task approve-design {slug}, '
                      f'then engpipe run {slug} --resume')
                raise SystemExit(3)
            if outcome:
                raise ValueError('design approval was not recorded')
            continue
        result = task(root, 'finish', slug)
        print(result.stdout.strip())
        return
    raise ValueError('workflow repeatedly changed phase; inspect the task before resuming')


def run(args, core):
    root = core.project_root()
    directory = core.tdir(root, args.slug)
    manifest = directory / 'runner.json'
    private_paths = [manifest, directory / 'state.json', directory / 'objects', directory / 'quarantine.json',
                     directory.parent / '.runner-intent.json', directory.parent / '.runner-worktree.lock']
    verify_private_paths(root, *private_paths)
    if args.recover_attempt and any((args.host, args.kind, args.size, args.prompt, args.prompt_file)):
        raise ValueError('recovery acknowledges an attempt without launching another one')
    with file_lock(directory.parent / '.runner-worktree.lock', timeout=0):
        verify_private_paths(root, *private_paths)
        if pending(root, directory.parent / '.runner-intent.json', args, core):
            return
        state_exists = (directory / 'state.json').is_file()
        if manifest.exists():
            if not args.resume:
                raise ValueError(f'task already has a run; use engpipe run {args.slug} --resume')
            request = json.loads(manifest.read_text(encoding='utf8'))
            if not isinstance(request, dict) or request.get('slug') != args.slug:
                raise ValueError('saved runner request does not match the task')
            if any((args.prompt, args.prompt_file, args.size, args.kind, args.host)):
                raise ValueError('resume uses the saved request, size, kind and host')
        else:
            if state_exists != args.resume:
                raise ValueError('use --resume to adopt an existing task; omit it when creating a task')
            prompt = args.prompt_file.read_text(encoding='utf8') if args.prompt_file else args.prompt
            if not prompt or not prompt.strip():
                raise ValueError('supply --prompt or --prompt-file for the first console run')
            state = core.get_state(root, args.slug) if state_exists else {}
            host = args.host or state.get('routing', {}).get('host')
            if not host:
                raise ValueError('select --host for the first run')
            if state.get('routing', {}).get('host', host) != host:
                raise ValueError('use the host recorded for this task')
            if state and ((args.size and args.size != state['size']) or (args.kind and args.kind != state['kind'])):
                raise ValueError('size and kind must match the existing task')
            if not state and not args.size:
                raise ValueError('choose --size small or large for a new task')
            request = {'slug': args.slug, 'host': host, 'kind': state.get('kind') or args.kind or 'feature',
                       'size': state.get('size') or args.size, 'prompt': prompt}
            save(root, manifest, request)
        if not state_exists:
            task(root, 'start', args.slug, '--host', request['host'], '--size', request['size'], '--kind', request['kind'])
        advance(root, directory, request, args, core)


def positive(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('expected a positive integer')
    return parsed


def add_runner_command(commands, core):
    parser = commands.add_parser('run', help='run or resume a task through a host CLI')
    parser.add_argument('slug')
    parser.add_argument('--host', choices=routing.HOSTS)
    parser.add_argument('--kind', choices=['feature', 'bugfix', 'refactor'])
    parser.add_argument('--size', choices=['small', 'large'])
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--prompt')
    source.add_argument('--prompt-file', type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--resume', action='store_true')
    action.add_argument('--recover-attempt', metavar='ID', help='acknowledge an interrupted writer after reconciliation')
    parser.add_argument('--timeout', type=positive, default=3600, help='seconds per host process')
    parser.add_argument('--max-rounds', type=positive, default=3, help='maximum repair rounds per invocation')
    parser.set_defaults(fn=lambda args: run(args, core))
