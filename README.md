# Engineering Agent Pipeline

Run a software task through a planner, an implementer and two independent reviews. Use your existing coding host interactively or start the workflow from a terminal. Both paths use the same Git working tree, task directory, test evidence and completion rules.

## Start here

You need Python 3.11 or later, Git, and an installed, authenticated coding host for live work. Choose `codex`, `opencode` or `claude`. Other hosts can use the task CLI manually. Adding a host to the automated runner also requires an adapter and protocol tests.

```sh
# From this configuration repository (Linux, macOS or WSL)
bash install.sh codex
```

```powershell
# From this configuration repository (native Windows PowerShell)
.\install.ps1 -Target codex
```

In the **application repository**, start from a Git working tree with at least one commit. Run `engpipe init` and configure `.engineering-pipeline/checks.json`. Give each changed component an owner, real targeted checks and its downstream dependencies. Set the full checks that decide whether your application passes. The empty initial manifest deliberately stops completion.

| How you work | Entry point |
|---|---|
| In an interactive coding session | Start the selected host's orchestrator in your application directory and give it the task. [Per-host instructions](docs/ADAPTERS.md) |
| From a terminal | Use `engpipe run --help` for start and resume commands, host selection, approval and failure handling. [Terminal workflow](docs/RUN.md) |
| With your own automation | Use `engpipe task --help` and the saved review inputs. [Role contracts](docs/ROLE_CONTRACTS.md) |

## What it checks

- **Small tasks** go through preflight, source-based analysis, implementation, targeted checks, the full gate and both code reviews. A large task adds one architect selected by risk, two design reviews and a human approval before implementation.
- **After a slice**, the CLI checks the changed components and transitive consumers listed in the manifest. The full gate checks the complete configured suite. A failed or missing check stops completion.
- **Before dispatch**, take a design or code review input. A and B get the same identifier and inspect the work separately. Record what each checked. A stale result belongs to the old input.
- **Model selection** starts with the host configuration. Two separate review contexts are required. OpenCode teams can opt into per-role models and require the author and both reviewers to use different declared families. A model name in a local record is operator-provided metadata.
- **Recovery** uses the task directory. Preserve it when resuming work and keep it out of Git. The component/check manifest can be shared with the application team.
- **Trust boundary**: the user controls the repository, the host, the tests and the local evidence. The CLI checks consistency between them. Use your normal CI and repository access controls for protected releases.

See the [installation and migration guide](docs/INSTALL.md), [host adapters](docs/ADAPTERS.md), [test selection and gate reuse](docs/TESTS.md) and [role contracts](docs/ROLE_CONTRACTS.md).

## Verify this repository

```sh
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -v
```

The test dependency parses actual YAML. The CLI uses the Python standard library. CI defines Linux, macOS and Windows jobs. Offline host fixtures test process arguments, protocol and recovery. A live model invocation additionally requires an installed host, its credentials and an available model.
