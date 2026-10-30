# Host adapters: OpenCode, Claude Code, Codex

All hosts use the **same on-disk task state and `engpipe` CLI**. There is no universal standard for subagent declarations, permission enforcement, provider IDs, session identifiers or runtime logs. The adapters adapt **role prompts**, not magically unify all tool APIs.

## OpenCode V2 (supported main path)

```bash
bash install.sh opencode
engpipe doctor
```

Installs 10 `.md` roles as symlinks in `~/.config/opencode/agents/`, sets default `pipeline-orchestrator`, preserves existing provider credentials/settings with a backup. Requires OpenCode V2 and authorized model IDs from current profile. Global `AGENTS.md` is symlinked; project AGENTS.md may add local constraints. See official [OpenCode agent docs](https://opencode.ai/v2/docs/agents).

## Claude Code (role export, best effort)

```bash
bash install.sh claude
```

Writes `.md` role prompts under `~/.claude/agents/`. Claude Code supports its own frontmatter and model identifiers; it will not accept OpenCode's model selectors or permission lists verbatim. Export intentionally leaves `model` unspecified. Choose supported models in Claude Code and arrange a separate external provider if distinct model *families* are required for two independent reviews. **Native Claude-only models share the Anthropic family**; two Claude subagents are not independent by our family rule. `engpipe` can still validate task state and gates. See [Claude custom agents](https://claude.com/blog/subagents-in-claude-code).

## Codex (role export, best effort)

```bash
bash install.sh codex
```

Writes `.toml` role prompts under `~/.codex/agents/` using `name`, `description`, `developer_instructions` as documented for Codex custom agents. Per-agent model is deliberately left to Codex/account configuration and may not support all OpenRouter providers. Codex subagents inherit parent sandbox/approval scope. Configure actual model routing outside this generator and run independent reviews externally when necessary. See [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Design principles

- Session IDs from any host are **optional** audit metadata. A new session may recover from saved task artifacts.
- Model IDs from different providers are not interchangeable. For externally routed review/author models record the **actual** identity via `--model` / `--author-model` arguments. Provider/model names entered by an agent are not runtime attestation; validate with actual host logs for high-risk work.
- Reviewer A and B must be distinct families and isolated from each other. If the host cannot achieve this, do not certify `DONE` as independently reviewed; arrange external second reviewer, or stop certification without losing task progress.
- Neither a role export nor an agent prompt guarantees automatic orchestration on its own. The integration must be E2E tested for the selected tool; all deterministic gates work independently of the host.
