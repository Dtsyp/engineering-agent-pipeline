"""Resolve host entry points without invoking command interpreters."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading


PACKAGES = {
    'codex': ('@openai/codex',),
    'claude': ('@anthropic-ai/claude-code',),
    'opencode': ('@opencode/cli', 'opencode-ai'),
}


def executable_argv(host):
    program = shutil.which(host)
    if not program:
        raise ValueError(f'{host} CLI missing from PATH')
    entry = Path(program)
    if os.name != 'nt' or entry.suffix.lower() == '.exe':
        return [program]
    if entry.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        # Node's standard global prefix on Windows places shims next to node_modules.
        # Resolve the installed package's bin field; never evaluate the shim itself.
        for name in PACKAGES.get(host, ()):
            package = entry.parent / 'node_modules' / name
            metadata = package / 'package.json'
            if not metadata.is_file():
                continue
            manifest = json.loads(metadata.read_text(encoding='utf8'))
            if manifest.get('name') != name:
                continue
            binary = manifest.get('bin')
            relative = binary.get(host) if isinstance(binary, dict) else binary
            if not isinstance(relative, str):
                continue
            target = (package / relative).resolve()
            if not target.is_file() or not target.is_relative_to(package.resolve()):
                continue
            if target.suffix.lower() == '.exe':
                return [str(target)]
            node = shutil.which('node.exe')
            with target.open('rb') as script:
                header = script.read(100)
            if node and (target.suffix.lower() in ('.js', '.cjs', '.mjs') or header.startswith(b'#!/usr/bin/env node')):
                return [node, str(target)]
    raise ValueError(f'install {host} as a native executable or a supported npm package with node.exe')


def stop_tree(process):
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # A provider-owned background service can outlive the CLI; intent recovery
        # requires reconciliation regardless of whether tree termination succeeded.
        killer = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
        try:
            subprocess.run([str(killer), '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
    process.wait()


def communicate(command, *, cwd, stdin_bytes, stdout, timeout):
    """Capture structured stdout up to 64 MiB and discard provider diagnostics."""
    maximum = 64 * 1024 * 1024
    with tempfile.TemporaryFile() as prompt:
        prompt.write(stdin_bytes)
        prompt.seek(0)
        process = subprocess.Popen(command, cwd=cwd, stdin=prompt, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=os.name == 'posix')
        oversized = threading.Event()
        def drain():
            with process.stdout as pipe:
                while chunk := pipe.read(65536):
                    if stdout.tell() + len(chunk) > maximum:
                        oversized.set()
                        continue
                    if not oversized.is_set():
                        stdout.write(chunk)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
            reader.join(timeout=2)
            if reader.is_alive():
                raise ValueError('host exited with a child still writing output')
            if oversized.is_set():
                raise ValueError('host exceeded the 64 MiB protocol limit')
        except BaseException:
            stop_tree(process)
            reader.join(timeout=2)
            raise
        return process.returncode
