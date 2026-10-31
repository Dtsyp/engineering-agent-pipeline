# Agent Role Contracts

This document defines the shared, **tool-neutral** handoff and quality standard for the ten specialized engineering roles. The Markdown files in `agents/` are authoritative role-specific instructions. Model IDs and runtime access rules are configuration details, not role identity.

## Durable task handoff

Each handoff should include, when applicable:
- Task slug, original user request, scope classification and approval state.
- READY `preflight.json` with scope/non-goals, constraints, accepted product decisions and no unresolved blocking questions.
- `analysis-context.json` with concise `path:line | verified claim` evidence, affected components/consumers, tests, risks and unknowns.
- Source evidence (`path:line`), affected components and transitive consumers.
- Acceptance criteria, frozen tests, relevant code/design fingerprint, approved `design.md` and slice assignment.
- Current implementation diff, latest test/gate evidence and open reviewer findings.
- Verified model invocation metadata *if available*, without inventing session IDs.

The canonical task state lives under `.engineering-pipeline/tasks/<slug>/`. Session IDs are optional hints. If a prior conversation disappears, hydrate a fresh agent from durable artifacts and continue. Never silently skip important checks because context is missing.

## Review output contract

Reviewers return one JSON object:

```json
{
  "verdict": "PASS",
  "blockers": 0,
  "findings": [],
  "checked": ["specific contracts and evidence inspected"],
  "uncertainties": []
}
```

Findings contain `severity` (`BLOCKER`, `MAJOR`, `MINOR`), `location`, `evidence`, `impact`, and `suggested_fix`. Any material unresolved blocker or major defect => `FIX`. An unsupported concern belongs in `uncertainties`, not as a fabricated blocker. The Orchestrator translates real reviewer results into `engpipe task design-review/code-review ... --findings ...`; reviewers never certify themselves or modify state.

Both reviews target exactly one **same fingerprint**. Reviewers cannot read each other's conclusions before completing theirs. They must represent three distinct underlying model families together with the author (architect or implementer); static routing alone is not runtime attestation.

Review A and B may run concurrently only when the host provides isolated fresh child/background contexts and can collect both results without cross-sharing. No universal host API is assumed. When that capability is absent or fails, invoke A and B sequentially in fresh contexts and withhold the first report from the second. A partial parallel batch may retain a completed report only if its fingerprint and isolation are still demonstrable; otherwise rerun both. In every mode two reviews remain mandatory.

## Test cadence

After each approved implementation slice: targeted checks for changed components and all dependent consumers (`engpipe task slice-check <slug>`), selected by `.engineering-pipeline/checks.json`. Unknown changed paths require justified map updates; they must not silently pass.

After the final slice: obtain authoritative full-suite evidence (`engpipe task gate <slug>`), then two independent code reviews. The command reuses an earlier PASS only when its strict validation-context fingerprint matches: code, full-check definition, `checks.json`, declared dependency/config files, runtime/probe identity, selected environment variables and the saved log hash. Incomplete cache declarations or failed probes disable reuse and run the suite. If implementation changes after FIX, run new targeted checks, obtain full-gate evidence and repeat **both** reviews on the new fingerprint.

## Escalation and safe failure

- Missing/expired session ID, host change, agent rename, lost context: reconstruct and continue, no automatic refusal.
- Unknown requirements/contracts: request analyst discovery or human decision; do not guess high-stakes semantics.
- Missing human approval on large design, failed tests, stale fingerprint, reviewer-family collision, no authoritative evidence: block `DONE` and state exactly what is missing.
- Any inability to run independent review via one host: use an authorized external review channel, record evidence, and make independence claims only if verified.
- Never weaken protected acceptance tests, force green results, conceal skipped checks, or commit/push without explicit authorization.

## Platform independence

OpenCode, Claude Code and Codex have different subagent, permission and model selection capabilities. The durable task schema and deterministic `engpipe` gates are portable; identical orchestration and automatic role-model routing are not guaranteed. Always test each adapter under its own host, and disclose unavailable capabilities.
