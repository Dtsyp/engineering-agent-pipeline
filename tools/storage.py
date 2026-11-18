"""Atomic UTF-8 files and process-scoped locks on Windows and POSIX."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import tempfile
import time


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', errors='surrogateescape', newline='\n',
                                     prefix='.' + path.name + '-', dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(path, timeout=30):
    """Serialize the entire read/modify/write operation, releasing on process exit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            def acquire():
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'command already in progress ({path}); retry after it exits') from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def verify_private_paths(root, *paths):
    """Require a genuinely ignored task subtree and regular, non-linked path components."""
    import stat
    import subprocess

    tasks = root / '.engineering-pipeline' / 'tasks'
    checked = {tasks}
    for value in paths:
        path = root / Path(value)
        if '..' in path.parts or not path.is_relative_to(tasks):
            raise ValueError(f'private artifact is outside the task directory: {path}')
        checked.add(path)
    visited = set()
    for path in checked:
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current in visited:
                continue
            visited.add(current)
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if (getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise ValueError(f'private task artifact crosses a symlink, junction or special file: {current}')
    tracked = subprocess.run(['git', 'ls-files', '-z', '--', '.engineering-pipeline/tasks'], cwd=root,
                             capture_output=True)
    if tracked.returncode or tracked.stdout:
        raise ValueError('cannot store private task data in a tracked or unavailable task directory')
    # Git cannot re-include descendants of an ignored directory. Check that
    # boundary as a directory, as well as every actual control/artifact path.
    names = {os.fsencode(path.relative_to(root).as_posix()) for path in checked - {tasks}}
    names.add(b'.engineering-pipeline/tasks/')
    result = subprocess.run(['git', 'check-ignore', '-z', '--stdin'], cwd=root,
                            input=b'\0'.join(sorted(names)) + b'\0', capture_output=True)
    ignored = set(result.stdout.split(b'\0')) - {b''}
    if result.returncode or ignored != names:
        raise ValueError('project Git rules expose private task data; run engpipe init and ensure /.engineering-pipeline/tasks/ is ignored')
