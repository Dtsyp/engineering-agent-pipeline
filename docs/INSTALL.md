# Installation and upgrades

Keep the checkout in a stable location. The installer copies the selected host's role definitions and creates an entry point to this checkout using the current Python interpreter. Install Python 3.11+, Git and the host you intend to use. Provider sign-in stays in that host.

## Install on one workstation

From the checkout on Linux or macOS:

```sh
./install.sh codex                 # or claude / opencode
# Equivalent, using a particular interpreter:
python3 tools/pipeline.py install --target codex
```

In native Windows PowerShell:

```powershell
.\install.ps1 -Target codex
# Equivalent when PowerShell script execution is restricted:
py -3 .\tools\pipeline.py install --target codex
```

Run the installer once for each host. Add the printed launcher directory to your user's PATH. The default is `~/.local/bin` on POSIX and `%LOCALAPPDATA%\engpipe\bin` on Windows. The generated PowerShell launcher calls Python directly and preserves argument boundaries. In Command Prompt or where unsigned PowerShell scripts are restricted, call `py -3 "C:\path to checkout\tools\pipeline.py" ...`. Quoting at the invoking shell still applies. Use PowerShell single-quoted arguments for literal free text or the terminal runner's `--prompt-file` option for multiline requests. WSL installations use the Linux commands, tools and home.

Set `ENGPIPE_BIN_DIR` before installation to choose a different launcher directory. `ENGPIPE_CONFIG_HOME` chooses the directory containing engpipe's routing, installation manifests and backups. Otherwise it uses `$XDG_CONFIG_HOME/engpipe` (default `~/.config/engpipe`) or `%APPDATA%\engpipe` on Windows. Host roles use the selected host's own configuration directory. Keep these overrides consistent between installation and use.

```sh
engpipe doctor --target codex
```

`doctor` checks the installed launcher and roles, host executable and supported diagnostics. Its output distinguishes local file verification from the host's effective configuration. Test model credentials and paid endpoints separately.

## Reinstall, migrate, recover

The installer leaves global instructions, default host agents, provider settings and unrelated role names alone. Ordinary reinstall updates the exact files installed by engpipe. If a user edited a role, or a pre-existing file occupies one of those names, installation stops before replacing it. Inspect the collision, then use `engpipe install --target HOST --replace` or Windows `.\install.ps1 -Target HOST -Replace` to archive the originals and install the managed versions. Each backup directory contains a path index; archived symlinks record their original target. Old timestamped role-backup directories created by the former exporter are moved out of the host's role search path during migration.

On older versions, `profile use` edited the checkout and the OpenCode installer replaced the global instructions and default agent. Preserve any customized checkout before pulling. Installing in a second clean clone lets you retain the old files while transferring custom model choices using `model set`. Restore a previous global `AGENTS.md` or default agent from your old OpenCode backup if you want to reverse the old installer's changes. Existing Codex `$CODEX_HOME` and Claude `$CLAUDE_CONFIG_DIR` are respected.

After pulling a new version, rerun install for each host you use, and start a fresh host session. Reinstall also refreshes the launcher after you move the checkout or its Python interpreter. If an update is interrupted, rerun installation for the same target. The journal remembers the selected policy and only accepts previous or expected contents. A conflicting manual edit still requires a decision. Avoid replacing the CLI or host files during active work.

## Choose routing

A new installation inherits the host model and uses independent review contexts. The policy and declared models are captured when a task starts. For native model selectors:

```sh
engpipe model list --target codex
engpipe model set pipeline-implementer YOUR-CODEX-MODEL --target codex
engpipe model set pipeline-code-a inherit --target codex
```

To configure family diversity, provide each author's and reviewer's host-native selector and underlying family with `model set ROLE SELECTOR --family FAMILY --target HOST`. Then use `model policy families --target HOST` and `doctor --target HOST`. You can stage a partial route. An incomplete or colliding strict route fails verification. Only enable strict mode on a host that can actually invoke all required models. Different gateway aliases do not establish family diversity.

The previous max-quality, balanced and budget profiles remain optional **OpenCode** examples. Verify available models in your account before applying one.

```sh
engpipe profile list --target opencode
engpipe profile use balanced --target opencode
engpipe doctor --target opencode
engpipe profile use inherit --target opencode
```

## Initialize the application

From the target application checkout, run `engpipe init`. Put real test, type-check and build commands into `.engineering-pipeline/checks.json`, then share that file with the application team. Tasks and their evidence remain local. Each writer works in its own Git worktree when separate tasks or interactive sessions run in parallel. See [test selection](TESTS.md), [interactive adapters](ADAPTERS.md) and the [terminal workflow](RUN.md).
