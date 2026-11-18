"""Install host-native roles and CLI entry points without rewriting the checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

import routing
from host_process import executable_argv
from storage import atomic_text, file_lock
from routing import HOSTS, PRESETS, REPO, REVIEWERS, ROLES


def agent_parts(role):
    return read_role(REPO / 'agents' / (role + '.md'))


def read_role(path):
    text = path.read_text(encoding='utf8')
    header, closing, body = text.removeprefix('---\n').partition('\n---\n')
    if not text.startswith('---\n') or not closing:
        raise ValueError('invalid agent frontmatter: ' + str(path))
    values = [line.removeprefix('description:').strip()
              for line in header.splitlines() if line.startswith('description:')]
    if len(values) != 1:
        raise ValueError('expected one description: ' + str(path))
    value = values[0]
    if value.startswith('"'):
        value = json.loads(value)
    elif not re.fullmatch(r'[^\W\d_][\w ,./()-]*', value) or value.lower() in {
            'yes', 'no', 'true', 'false', 'on', 'off', 'null', 'y', 'n'}:
        raise ValueError('use a JSON-quoted description in ' + str(path))
    if not isinstance(value, str) or not value.strip():
        raise ValueError('empty role description in ' + str(path))
    return value, body.strip()


def role_dir(host):
    if host == 'opencode':
        config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'opencode'
    elif host == 'claude':
        config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    elif host == 'codex':
        config = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    else:
        raise ValueError('unsupported host: ' + str(host))
    return config.expanduser().absolute() / 'agents'


def role_file(host, role):
    extension = '.toml' if host == 'codex' else '.md'
    return role_dir(host) / (role + extension)


def interactive_hint(host):
    return {
        'opencode': 'open the OpenCode TUI, use /agents, select pipeline-orchestrator',
        'claude': 'claude --agent pipeline-orchestrator',
        'codex': 'start Codex and enter $pipeline-orchestrator followed by your task',
    }[host]


def host_body(host, role, body):
    if role == 'pipeline-orchestrator':
        return body + ('\n\nStart tasks with `engpipe task start <slug> --host ' + host +
                       ' --kind <kind> --size <size>`. Use this host’s child dispatch for each role.')
    return body


def role_text(host, role, policy):
    description, body = agent_parts(role)
    body = host_body(host, role, body)
    model = policy['models'][role]
    if host == 'codex':
        fields = {'name': role, 'description': description, 'developer_instructions': body}
        if model:
            fields['model'] = model
        text = ''.join(key + ' = ' + json.dumps(value, ensure_ascii=False) + '\n'
                       for key, value in fields.items())
        if role in REVIEWERS:
            text += ('\n[features]\napps = false\nplugins = false\n'
                     'request_permissions_tool = false\n')
        return text
    if host == 'opencode':
        original = (REPO / 'agents' / (role + '.md')).read_text(encoding='utf8')
        header = original.split('\n---\n', 1)[0]
        # The same role can be a native child or the top-level headless worker.
        header = header.replace('\nmode: subagent\n', '\nmode: all\n')
    else:
        header = '---\nname: ' + role + '\ndescription: ' + json.dumps(description, ensure_ascii=False)
        tools = 'Read, Glob, Grep'
        if role == 'pipeline-orchestrator':
            workers = ', '.join(item for item in ROLES if item != role)
            tools += ', Bash, Agent(' + workers + ')'
        elif role == 'pipeline-implementer':
            tools += ', Bash, Write, Edit'
        header += '\ntools: ' + tools
    if model:
        header += '\nmodel: ' + json.dumps(model)
    return header + '\n---\n\n' + body + '\n'


def skill_path():
    return Path.home() / '.agents' / 'skills' / 'pipeline-orchestrator' / 'SKILL.md'


def exports(host, policy, roles=None):
    files = {role_file(host, role): role_text(host, role, policy) for role in (roles or ROLES)}
    if host == 'codex' and (roles is None or 'pipeline-orchestrator' in roles):
        _, body = agent_parts('pipeline-orchestrator')
        files[skill_path()] = ('---\nname: pipeline-orchestrator\n'
                               'description: Coordinate an engpipe engineering task when explicitly requested.\n'
                               '---\n\n' + host_body(host, 'pipeline-orchestrator', body) + '\n')
    return files


def launcher():
    bindir = os.environ.get('ENGPIPE_BIN_DIR')
    if os.name == 'nt':
        directory = Path(bindir or Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
                         / 'engpipe' / 'bin')
        # The BOM lets Windows PowerShell 5.1 read Unicode install paths.
        template = (REPO / 'tools/windows-launcher.ps1').read_text(encoding='utf8')
        paths = {'__PYTHON__': sys.executable, '__ENTRY__': str(REPO / 'tools/pipeline.py')}
        script = re.sub(r'__PYTHON__|__ENTRY__', lambda m: "'" + paths[m[0]].replace("'", "''") + "'", template)
        return directory / 'engpipe.ps1', '\ufeff' + script
    directory = Path(bindir or Path.home() / '.local' / 'bin')
    return directory / 'engpipe', ('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' '
                                    + shlex.quote(str(REPO / 'tools' / 'pipeline.py')) + ' "$@"\n')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest_path(host):
    return routing.data_dir() / ('installed-' + host + '.json')


def installed_manifest(host):
    path = manifest_path(host)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ValueError('invalid installation manifest: ' + str(path))
    return value


def journal_path(host):
    return routing.data_dir() / ('installing-' + host + '.json')


def install(host, policy, replace=False):
    journal = journal_path(host)
    previous = json.loads(journal.read_text(encoding='utf8')) if journal.exists() else {}
    if previous:
        policy = previous['policy']
    files = exports(host, policy)
    wrapper, script = launcher()
    files[wrapper] = script
    # The launcher is shared by all hosts. Keep ownership when reinstalling
    # from a moved checkout or another Python environment.
    known = {}
    for target in HOSTS:
        for path, checksum in installed_manifest(target).items():
            known.setdefault(path, set()).add(checksum)
    pending = []
    before = {}
    for path, content in files.items():
        if not path.is_symlink() and path.exists() and not path.is_file():
            raise ValueError('installation target is not a regular file: ' + str(path))
        link = path.is_symlink()
        old = path.read_bytes() if not link and path.is_file() else None
        identity = ('link:' + os.readlink(path)) if link else digest(old) if old is not None else None
        before[str(path)] = identity
        data = content.encode('utf8')
        if old == data and not path.is_symlink():
            if path == wrapper and os.name != 'nt' and not os.access(path, os.X_OK):
                pending.append((path, data))
            continue
        if path.is_symlink() or old is not None:
            owned = old is not None and digest(old) in known.get(str(path), set())
            preapproved = previous.get('before', {}).get(str(path)) == identity
            if not (owned or replace or preapproved):
                raise ValueError(f'existing unmanaged or edited file {path}; inspect and use install --replace to back it up')
        pending.append((path, data))
    # Old exporters placed backups in the recursively discovered role directory.
    legacy = [path for path in role_dir(host).glob('.engineering-backup-*')
              if re.fullmatch(r'\.engineering-backup-\d{14}', path.name)
              and path.is_dir() and not path.is_symlink()
              and any((path / role_file(host, role).name).is_file() for role in ROLES)]
    if legacy and not replace and any(str(path) not in previous.get('legacy', []) for path in legacy):
        raise ValueError('legacy backups contain duplicate roles; run install --replace to archive them: ' + str(legacy[0]))
    # A killed process leaves this marker. Retry finishes the same policy and
    # accepts only the exact preapproved originals or already-rendered results.
    atomic_text(journal, json.dumps({'policy': policy, 'before': before,
                                     'legacy': [str(path) for path in legacy]}))
    backup = None
    if legacy:
        backup_root = routing.data_dir() / 'backups'
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=host + '-', dir=backup_root))
        for directory in legacy:
            shutil.move(str(directory), str(backup / directory.name))
        print('Archived old role backups in', backup)
    for path, data in pending:
        if path.exists() or path.is_symlink():
            if backup is None:
                backup_root = routing.data_dir() / 'backups'
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = Path(tempfile.mkdtemp(prefix=host + '-', dir=backup_root))
                print('Backup directory:', backup)
            old_name = digest(os.fsencode(path))[:16] + '-' + path.name
            if path.is_symlink():
                old_name += '.link.json'
                backup.joinpath(old_name).write_text(
                    json.dumps({'path': str(path), 'target': os.readlink(path)}), encoding='utf8')
            else:
                shutil.copy2(path, backup / old_name)
            with (backup / 'paths.jsonl').open('a', encoding='utf8') as log:
                log.write(json.dumps({'path': str(path), 'backup': old_name}) + '\n')
        atomic_text(path, data.decode('utf8'))
        if path == wrapper and os.name != 'nt':
            path.chmod(0o755)
    recorded = {str(path): digest(content.encode('utf8')) for path, content in files.items()}
    atomic_text(manifest_path(host), json.dumps(recorded, indent=2))
    atomic_text(routing.config_path(host), json.dumps(policy, indent=2) + '\n')
    journal.unlink()
    print('Installed', host, 'in', role_dir(host))
    print('CLI:', wrapper, '(add its directory to PATH)')
    print('Interactive entry:', interactive_hint(host))


def update_policy(host, policy):
    if manifest_path(host).exists():
        install(host, policy)
        return
    atomic_text(routing.config_path(host), json.dumps(policy, indent=2) + '\n')


def verify_installed(host, roles=None):
    if journal_path(host).exists():
        raise ValueError(f'interrupted installation: retry engpipe install --target {host}')
    for path, expected in exports(host, routing.task_policy(host), roles).items():
        if not path.is_file() or path.read_bytes() != expected.encode('utf8'):
            raise ValueError(f'{host} role missing or stale: {path}; run engpipe install --target {host}')


def checked_process(command, cwd=None, require_success=True):
    try:
        argv = [*executable_argv(command[0]), *command[1:]]
        result = subprocess.run(argv, cwd=cwd, capture_output=True, encoding='utf8',
                                errors='replace', timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'{command[0]} diagnostic unavailable: {exc}') from exc
    if require_success and result.returncode:
        raise ValueError(f'{command[0]} diagnostic failed (exit {result.returncode}): {command[1:]}')
    return result.stdout


def check_opencode_roles(roles, cwd=None):
    records = json.loads(checked_process(['opencode', 'debug', 'agents'], cwd))
    if not isinstance(records, list):
        raise ValueError('OpenCode returned an unrecognized agent catalog')
    loaded = {record['id']: record for record in records if isinstance(record, dict) and 'id' in record}
    models = routing.task_policy('opencode')['models']
    for role in roles:
        record = loaded.get(role)
        if not record or record.get('mode') not in ('all', 'primary'):
            raise ValueError('OpenCode role unavailable as a main agent: ' + role)
        if models[role]:
            model = record.get('model') or {}
            if not isinstance(model, dict):
                raise ValueError('unknown effective OpenCode model format for ' + role)
            actual = model.get('providerID', '') + '/' + (model.get('id') or model.get('model', ''))
            selector, _, variant = models[role].partition('#')
            if actual != selector or (variant and variant != (model.get('variant') or record.get('variant'))):
                raise ValueError('OpenCode effective model differs for ' + role)
        if role in REVIEWERS:
            rules = record.get('permissions')
            if not isinstance(rules, list):
                raise ValueError('cannot verify OpenCode reviewer permissions for ' + role)
            if not all(isinstance(rule, dict) for rule in rules):
                raise ValueError('unrecognized OpenCode permission format')
            defaults = [index for index, rule in enumerate(rules)
                        if rule.get('action') == '*' and rule.get('resource') == '*'
                        and rule.get('effect') == 'deny']
            if not defaults:
                raise ValueError(f'OpenCode reviewer {role} lacks default-deny permissions')
            for rule in rules[defaults[-1] + 1:]:
                if rule.get('effect') != 'deny' and rule.get('action') not in ('read', 'glob', 'grep'):
                    raise ValueError(f'OpenCode reviewer {role} has non-read-only permissions')


def doctor(host):
    verify_installed(host)
    wrapper, script = launcher()
    if not wrapper.is_file() or wrapper.read_bytes() != script.encode() or not os.access(wrapper, os.X_OK):
        raise ValueError('CLI launcher is missing or stale: ' + str(wrapper))
    if not shutil.which(host):
        raise ValueError('host executable not found on PATH: ' + host)
    print(host, checked_process([host, '--version']).strip())
    routing.validate_routes(routing.task_policy(host))
    if host == 'opencode':
        check_opencode_roles(ROLES)
        selected = {model.partition('#')[0] for model in routing.task_policy(host)['models'].values() if model}
        if selected:
            catalog = set(checked_process(['opencode', 'models']).splitlines())
            missing = selected - catalog
            if missing:
                raise ValueError('configured models missing from local catalog: ' + ', '.join(sorted(missing)))
        print('OpenCode effective roles and reviewer restrictions checked.')
    elif host == 'codex':
        report = json.loads(checked_process(['codex', 'doctor', '--json'], require_success=False))
        checks = report.get('checks') if isinstance(report, dict) else None
        if not isinstance(checks, dict) or checks.get('config.load', {}).get('status') != 'ok':
            raise ValueError('Codex config.load unsuccessful; inspect codex doctor --json')
        print('Codex config.load passed. Native child permissions follow the parent session.')
    else:
        if '--agent' not in checked_process(['claude', '--help']):
            raise ValueError('Claude Code lacks the --agent entry point')
        print('Installed Claude files and CLI capabilities checked; effective runtime roles are unverified.')
    print('No model invocation or entitlement check performed.')


def add_host_commands(subparsers):
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument('--target', choices=HOSTS, default='opencode')
    installer = subparsers.add_parser('install', parents=[target])
    installer.add_argument('--replace', action='store_true', help='back up and replace colliding owned role names')
    installer.set_defaults(fn=cmd_install)
    subparsers.add_parser('doctor', parents=[target]).set_defaults(fn=lambda args: doctor(args.target))
    profiles = subparsers.add_parser('profile').add_subparsers(dest='op', required=True)
    for op in ('list', 'show', 'use'):
        parser = profiles.add_parser(op, parents=[target])
        if op != 'list':
            parser.add_argument('name', choices=PRESETS)
        parser.set_defaults(fn=cmd_profile)
    models = subparsers.add_parser('model').add_subparsers(dest='op', required=True)
    models.add_parser('list', parents=[target]).set_defaults(fn=cmd_model)
    choice = models.add_parser('set', parents=[target])
    choice.add_argument('role', choices=ROLES)
    choice.add_argument('selector', help='host selector or inherit')
    choice.add_argument('--family', help='declared underlying model family for strict review')
    choice.set_defaults(fn=cmd_model)
    policy = models.add_parser('policy', parents=[target])
    policy.add_argument('mode', choices=routing.MODES)
    policy.set_defaults(fn=cmd_model)


def cmd_install(args):
    with file_lock(routing.data_dir() / 'install.lock'):
        install(args.target, routing.task_policy(args.target), args.replace)


def cmd_profile(args):
    host = args.target
    if args.op == 'list':
        print('\n'.join(PRESETS if host == 'opencode' else ('inherit',)))
        return
    policy = routing.profile_policy(args.name, host)
    if args.op == 'use':
        with file_lock(routing.data_dir() / 'install.lock'):
            if journal_path(host).exists():
                install(host, policy)
            update_policy(host, policy)
    print(json.dumps(policy, indent=2))


def cmd_model(args):
    if args.op == 'list':
        print(json.dumps(routing.task_policy(args.target), indent=2))
        return
    with file_lock(routing.data_dir() / 'install.lock'):
        if journal_path(args.target).exists():
            install(args.target, routing.task_policy(args.target))
        _configure_model(args)


def _configure_model(args):
    policy = routing.task_policy(args.target)
    if args.op == 'set':
        model = None if args.selector == 'inherit' else args.selector
        if model is not None:
            routing.validate_selector(args.target, model)
        if args.family:
            if not model or not routing.FAMILY.fullmatch(args.family):
                raise ValueError('invalid explicit selector or declared family')
            policy['families'][model] = args.family
        policy['models'][args.role] = model
    elif args.op == 'policy':
        policy['mode'] = args.mode
    if args.op != 'list':
        update_policy(args.target, policy)
    print(json.dumps(policy, indent=2))
