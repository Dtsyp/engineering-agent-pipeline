# Agent Role Contracts

This document defines the shared, **tool-neutral** handoff and quality standard for the ten specialized engineering roles. The Markdown files in `agents/` are authoritative role-specific instructions. Model IDs and runtime access rules are configuration details, not role identity.

## Durable task handoff

Each handoff should include, when applicable:
- Task slug, original user request, scope classification and approval state.
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

## Test cadence

After each approved implementation slice: targeted checks for changed components and all dependent consumers (`engpipe task slice-check <slug>`), selected by `.engineering-pipeline/checks.json`. Unknown changed paths require justified map updates; they must not silently pass.

After the final slice: full authoritative suite (`engpipe task gate <slug>`), then two independent code reviews. If implementation changes after FIX, run new targeted checks and repeat the final full suite plus **both** reviews on the new fingerprint. A full gate is therefore "once on the final snapshot" rather than "exactly once no matter what".

## Escalation and safe failure

- Missing/expired session ID, host change, agent rename, lost context: reconstruct and continue, no automatic refusal.
- Unknown requirements/contracts: request analyst discovery or human decision; do not guess high-stakes semantics.
- Missing human approval on large design, failed tests, stale fingerprint, reviewer-family collision, no authoritative evidence: block `DONE` and state exactly what is missing.
- Any inability to run independent review via one host: use an authorized external review channel, record evidence, and make independence claims only if verified.
- Never weaken protected acceptance tests, force green results, conceal skipped checks, or commit/push without explicit authorization.

## Platform independence

OpenCode, Claude Code and Codex have different subagent, permission and model selection capabilities. The durable task schema and deterministic `engpipe` gates are portable; identical orchestration and automatic role-model routing are not guaranteed. Always test each adapter under its own host, and disclose unavailable capabilities.
