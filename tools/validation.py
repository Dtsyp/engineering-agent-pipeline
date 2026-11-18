"""Git-aware source snapshots and bounded subprocess output."""
import hashlib
import os
from pathlib import Path
import shlex
import stat
import subprocess
import threading
import tempfile

from host_process import stop_tree
from storage import atomic_text, verify_private_paths

OUTPUT_LIMIT = 100_000


def file_hash(path):
    if path.is_symlink():
        return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    if not path.is_file():
        return 'MISSING'
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True)
    if result.returncode:
        raise ValueError('git ' + args[0] + ': ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def file_snapshot(root):
    """Capture both the staged entry and worktree contents, modes and type."""
    entries = {}
    for record in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if record:
            metadata, path = record.split(b'\t', 1)
            mode, oid, stage = metadata.decode('ascii').split()
            if stage != '0':
                raise ValueError('resolve the Git merge conflict before validation: ' + os.fsdecode(path))
            entries[os.fsdecode(path)] = (mode, oid)
    for raw in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
        if raw:
            name = os.fsdecode(raw)
            # Python may create bytecode while the configured checks run. All other outputs
            # and source files follow the project's own Git ignore rules.
            if not (Path(name).parent.name == '__pycache__' and name.endswith('.pyc')):
                entries.setdefault(name, ('', ''))

    snapshot = {}
    for name, (mode, oid) in entries.items():
        if name == '.engineering-pipeline/checks.json' or name.startswith('.engineering-pipeline/tasks/'):
            continue
        path = root / name
        if mode == '160000':
            if not (path / '.git').exists():
                raise ValueError('initialize the submodule before validation: ' + name)
            if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'):
                raise ValueError('dirty submodule; validate and commit its changes before checking the parent: ' + name)
            value = 'submodule:' + git(path, 'rev-parse', 'HEAD').decode().strip()
        elif path.is_symlink():
            value = 'link:' + file_hash(path)
            # Git can create a plain file of equivalent bytes on Windows without symlink support.
        elif path.is_file():
            executable = bool(path.stat().st_mode & stat.S_IXUSR) if os.name != 'nt' else mode == '100755'
            value = ('x:' if executable else 'f:') + file_hash(path)
        elif path.exists():
            raise ValueError('unsupported directory or special file in Git snapshot: ' + name)
        else:
            value = 'missing'
        snapshot[name] = f'{mode}:{oid}:{value}'
    return snapshot


def capture_command(root, command, timeout):
    """Drain the whole output for its digest while retaining at most OUTPUT_LIMIT bytes."""
    digest = hashlib.sha256()
    tail = bytearray()
    clipped = False
    try:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=os.name == 'posix')
    except OSError as exc:
        return 1, str(exc), None

    def drain():
        nonlocal clipped
        with process.stdout as pipe:
            while chunk := pipe.read(65536):
                digest.update(chunk)
                tail.extend(chunk)
                if len(tail) > OUTPUT_LIMIT:
                    del tail[:-OUTPUT_LIMIT]
                    clipped = True
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    problem = ''
    try:
        process.wait(timeout=timeout)
        reader.join(timeout=2)
        if reader.is_alive():
            problem = 'command left a child holding its output pipe'
    except KeyboardInterrupt:
        stop_tree(process)
        reader.join(timeout=1)
        raise
    except subprocess.TimeoutExpired:
        problem = f'command timed out after {timeout}s'
    if problem:
        stop_tree(process)
        reader.join(timeout=1)
        return 1, problem + '\n' + bytes(tail).decode('utf8', errors='replace'), None
    output = ('[output truncated]\n' if clipped else '') + tail.decode('utf8', errors='replace')
    return process.returncode, output, digest.hexdigest()


def run_checks(root, commands, logfile):
    tail = ''
    ok = True
    for command in commands:
        code, output, _ = capture_command(root, command, 1800)
        tail = (tail + '$ ' + shlex.join(command) + '\n' + output + '\nexit=' + str(code) + '\n')[-OUTPUT_LIMIT:]
        if code:
            ok = False
            break
    atomic_text(logfile, tail)
    return ok


def tree_environment(directory):
    # Preserve uncommitted source only in the private task directory, outside the project's refs and objects.
    objects = directory / 'objects'
    objects.mkdir(exist_ok=True, parents=True)
    env = {**os.environ, 'GIT_OBJECT_DIRECTORY': str(objects.resolve())}
    env.pop('GIT_ALTERNATE_OBJECT_DIRECTORIES', None)
    return env


def worktree_tree(root, directory, snapshot=None):
    """Capture a self-contained tree without modifying the caller's Git refs, objects or index."""
    objects = directory / 'objects'
    # Git may create objects in any existing fanout directory. Reject linked components before it writes.
    existing = [objects]
    if objects.is_dir() and not objects.is_symlink():
        for parent, dirs, files in os.walk(objects, followlinks=False):
            existing.extend(Path(parent) / name for name in dirs + files)
    verify_private_paths(root, *existing)
    if snapshot is None:
        snapshot = file_snapshot(root)

    with tempfile.TemporaryDirectory() as scratch:
        env = {**tree_environment(directory), 'GIT_INDEX_FILE': str(Path(scratch) / 'index'), 'GIT_LITERAL_PATHSPECS': '1'}
        def indexed(*args, data=None):
            result = subprocess.run(['git', *args], cwd=root, env=env, input=data, capture_output=True)
            if result.returncode:
                raise ValueError('cannot capture initial Git tree: ' + result.stderr.decode(errors='replace'))
            return result.stdout
        indexed('read-tree', '--empty')
        # An empty temporary index needs explicit paths, including tracked files ignored by current rules.
        names = [os.fsencode(name) for name in snapshot if os.path.lexists(root / name)]
        if names:
            indexed('add', '--force', '--pathspec-from-file=-', '--pathspec-file-nul', data=b'\0'.join(names) + b'\0')
        tree = indexed('write-tree').decode().strip()
    return tree
