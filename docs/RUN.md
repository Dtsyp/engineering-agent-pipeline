# Running a task from the terminal

Install the adapter for the host you want to use. Sign in with that host's CLI, select its models if necessary, and configure real checks in the product repository. The host process runs from the repository root. It uses the current user's host configuration and provider access.

```sh
engpipe install --target codex
engpipe doctor --target codex
engpipe init
# Configure .engineering-pipeline/checks.json for this project.
engpipe run fix-checkout --host codex --kind bugfix --size small --prompt "Fix the checkout rounding regression"
engpipe run migrate-schema --host claude --kind feature --size large --prompt-file request.md
```

Use `--host opencode`, `--host codex` or `--host claude`. The commands work in a native terminal on the selected OS. In PowerShell use single quotes around prompts with dollar signs. A UTF-8 `--prompt-file` preserves a multiline request across shells.

**Choose small** for a bounded change with known contracts and acceptance checks. **Choose large** for architectural decisions, unclear impact, public contracts or migrations. Large tasks require two design reviews and explicit interactive approval as well as two final code reviews. The runner never approves a design.

## What runs

1. A coordinator starts in the chosen host, reads the saved request and task state, and dispatches native roles for outstanding work. Its responsibility ends at the next review boundary. The driver's readiness check calls the same task CLI used by an interactive agent.
2. For each review, the driver issues the core input ID and launches two fresh host processes. Both read the same packet of requirements, role policy and source evidence. Code reviewers also receive the worktree patch from task start (including new files and later commits), a staged patch against current HEAD, the full check manifest, its index identity and the gate log. For a migrated task whose starting tree was never recorded, the packet covers the entire present source tree and lists known historical deletions whose original contents could not be reconstructed. Both processes finish before any verdict is registered. Keep these private artifacts in the local worktree.
3. The driver checks the response schema and input ID and records the results through the core review handler. A FIX sends both actual reports to the coordinator. A changed subject needs a fresh pair. The first FIX closes the generation in task state. The complete pair remains in the runner checkpoint until repair succeeds.
4. In a terminal, design approval uses the interactive `engpipe task approve-design` command. In CI, the driver returns status 3 after two valid design approvals from reviewers, so a human can inspect `design.md`, record the approval in the same worktree and resume. Only a successful `engpipe task finish` certifies a newly completed task.

| Host | Coordinator | Independent reviews |
| --- | --- | --- |
| Codex | `codex exec`, workspace-write sandbox, installed role instructions | Two fresh `codex exec` calls in the read-only sandbox with plugins, apps, hooks, subagents, permission requests and configured MCP servers disabled |
| Claude Code | Print mode with the named orchestrator | Two print-mode sessions with the respective named agents, built-in Read, Glob and Grep tools, MCP tools disabled |
| OpenCode | `opencode run` with the named primary-capable orchestrator | Two `opencode run` sessions with named primary-capable reviewers after checking the effective role catalog and permissions |

The driver supplies no automatic grant for denied host tools. A provider outage, a missing entitlement or a permission prompt the host cannot satisfy can stop a run. Run the same task interactively when it needs host-specific input. On Windows the console driver requires a native executable or the recognized npm package with its Node entry point. It never evaluates a shell shim. Host-specific developer switches and third-party wrappers may need a native installation. [Codex noninteractive mode](https://developers.openai.com/codex/noninteractive/) · [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) · [OpenCode commands](https://opencode.ai/docs/cli/commands/)

## Resume and recover

```sh
engpipe task status fix-checkout
engpipe run fix-checkout --resume
```

Resume reconstructs the next step from persisted task evidence. It starts a fresh coordinator when more work is required. Completed inputs and matching review pairs survive loss of host session IDs. A partial pair interrupted before both replies were checkpointed can cost two more reviewer calls. If a native reviewer has already submitted a valid PASS for the current input, the driver preserves that record and dispatches only the outstanding focus. A native task can be adopted by supplying `--resume --prompt-file request.md` once. Later resumes read that saved request.

Before writing requests and control files, the driver checks that the task directory is ignored by Git and its storage path contains no symlinks or junctions. One console writer owns a Git worktree at a time. A linked worktree has its own lock and can run another task. Before starting a mutable host process, the driver records its task and attempt ID. A crash, timeout or unsuccessful coordinator exit leaves that intent in place even if the OS released its lock.

1. Stop any outstanding host work, including background children. Inspect the worktree diff and task evidence to decide which effects already landed.
2. Acknowledge that specific attempt with `engpipe run SLUG --recover-attempt ID`, using the slug and ID printed by the driver. This releases the worktree for another invocation.
3. Run `engpipe run SLUG --resume` when the evidence is consistent. If the coordinator wrote a verdict, approval, completion or task identity, the task is quarantined. Inspect the changes and start a fresh task from a reviewed baseline.

Use `--timeout SECONDS` to bound one host process (default 3600), and `--max-rounds N` for the permitted number of repair rounds per invocation (default 3). Exit 0 means completion or a successful explicit recovery acknowledgement. Exit 2 reports an operator error, rejected evidence or failed host action. Exit 3 requests interactive design approval.

The offline integration suite runs the complete CLI against host protocol doubles in temporary Git repositories. Installing and parsing roles exercises the host configuration. A model-backed run additionally requires a configured host, provider entitlement and real project checks.
