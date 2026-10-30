# Engineering pipeline — shared guidance

This is a portable, tool-agnostic engineering workflow for feature, bugfix and refactor tasks. Use the local CLI `engpipe` (or `python3 path/to/tools/pipeline.py`) for deterministic task state and gates; agents implement roles, models are replaceable per agent and via the three profiles.

Use `docs/ROLE_CONTRACTS.md` for consistent inputs, outputs, escalation, recovery and review evidence. Each role is further specified in its `agents/pipeline-*.md` file.

Every task: independent analysis → [if large/risky: architecture + two isolated design reviews + explicit human approval] → accountable implementation in approved vertical slices → targeted component AND downstream-service checks after each slice → ONE authoritative full validation after all slices → two independent code reviews → deterministic finish.

After FIX or any code change, update targeted checks, rerun full validation and BOTH code reviews on the new fingerprint. This is the only reason to run the full suite again in the ordinary workflow. Never call DONE with stale evidence or failed tests.

The orchestrator should determine size from risks and evidence, escalating unknown high-risk contracts to large. A missing agent session ID is not a work blocker: recover from on-disk task artifacts and continue. Session IDs are optional audit hints, not stable task identity. Genuine code errors, uncovered file impacts, unavailable independent reviewers, absent human approval, unverified model identity and failed gates **must never** be silently bypassed.

Use distinct underlying model families for review A and B and different from the relevant author. Gateway name is not model family. Report that model independence is **configuration-verified only** unless real invocation records are inspected. Do not share reviewer reports until both are complete. Never silently change frozen acceptance tests or commit/push without authorization.

Set up `.engineering-pipeline/checks.json` in each project. Every component lists file patterns, upstream `depends_on`, and targeted commands; the dependency graph determines downstream checks. Unknown changed paths require updating this map, not skipping tests. `full_checks` are the authoritative project validation. No tokens, passwords, production code, API keys or private task history belong in this configuration repository.

Platform adapters (OpenCode, Claude Code, Codex) have different capabilities. Never assume all three support the same providers, subagent permissions or session metadata. The CLI's correctness gates are portable; orchestration is best effort unless proven with integration tests. Use an external or independent review channel when the host cannot provide distinct model families.
