# Interactive host adapters

Install the chosen host adapter and run its diagnostics from the application repository. Every orchestrator starts tasks with the actual `--host`. Use `engpipe task` for all state transitions, including immutable review inputs and verdicts. For a terminal process that owns dispatch, resumption and result collection, see [terminal workflow](RUN.md).

| Host | Interactive entry | Installed files | Review permissions |
|---|---|---|---|
| OpenCode V2 | Open the TUI, type `/agents`, select `pipeline-orchestrator`, and enter the task. | `~/.config/opencode/agents/pipeline-*.md` or the XDG config root. | Reviewers deny tools by default and allow source inspection through read, glob and grep. |
| Claude Code | From your project, run `claude --agent pipeline-orchestrator` and enter the task. | `~/.claude/agents/pipeline-*.md`, respecting `CLAUDE_CONFIG_DIR`. | Reviewer tools are limited to Read, Glob and Grep. The main orchestrator can dispatch the named child roles and run the CLI. |
| Codex | Start Codex and enter `$pipeline-orchestrator` followed by the task in the **main conversation**. The same explicit skill applies in clients using this local Codex installation. | `$CODEX_HOME/agents/pipeline-*.toml` (default `~/.codex/agents`) and a main-conversation skill at `~/.agents/skills/pipeline-orchestrator/SKILL.md`. | Native custom agents inherit the parent sandbox. The installed reviewers disable apps, plugins and new permission requests. A dedicated read-only process can enforce filesystem isolation for a terminal review. |

**OpenCode.** The orchestrator has primary mode. Workers support both standalone dispatch and native child use. An explicit role model is rendered only when configured. The active TUI session can independently select its own model. `doctor --target opencode` compares effective `debug agents` output, checks reviewer permission rules and compares explicit selectors with the local catalog. Project or managed configuration can change the effective result. [Agents](https://opencode.ai/docs/agents) · [TUI selection](https://opencode.ai/docs/cli/tui) · [CLI commands](https://opencode.ai/docs/cli/commands)

**Claude Code.** Starting the orchestrator as the main agent gives it the documented named-child allowlist. After the first installation, use a fresh session. Project or managed definitions may take precedence over user roles. Inspect the effective role if your organization has overrides. The CLI doctor verifies export contents, binary version and availability of the main-agent flag. [Claude custom agents](https://code.claude.com/docs/en/sub-agents) · [CLI reference](https://code.claude.com/docs/en/cli-reference)

**Codex.** The installed skill gives the main thread the orchestrator's instructions. Use the TOML workers for child dispatch. The CLI doctor verifies exports and the host's `config.load` diagnostic. It does not launch a paid model or independently attest reviewer identity. With a writable parent, maintain separate reviewer contexts and review actual host actions. Run separate writers in their own worktrees. [Codex subagents and inherited settings](https://developers.openai.com/codex/subagents) · [Codex skills](https://developers.openai.com/codex/skills)

## Shared contract

- Preserve the A/B boundary in parallel or sequential execution. Each reviewer receives the same captured input identifier and independently returns evidence. Record the original identifier when submitting the result.
- A session ID is optional audit metadata. Reconstruct a lost session from the task files. A reported model ID or family is a declaration unless independently observed in host records.
- A compatible new host needs its own entry point, supported model-selector mapping, permission model and integration tests. Its workers can also use the lower-level task CLI.
