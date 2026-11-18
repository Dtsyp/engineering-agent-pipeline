# Role contracts

| Role | Receives | Returns |
|---|---|---|
| Orchestrator | Request, project constraints, durable task state | Scope, decisions, phase changes, test evidence and recorded reviewer outcomes |
| Analyst | Ready preflight and repository | Relevant contract, concrete `path:line \| claim` evidence, dependencies, tests and unknowns |
| Architect (one of L1/L2/L3) | Reviewed scope and evidence for a large task | A design, alternatives, invariants, rollout and acceptance conditions |
| Design A / B | The same proposed design and review input in separate contexts | Two reports, each tied to the dispatched design |
| Implementer | Agreed scope, frozen tests, approved design where required, prior actionable findings | One coherent change, affected components and verification details |
| Code A / B | The same working tree, requirements, verified full-gate evidence and review input | Two reports, each tied to the dispatched code and gate |

Use the analysis context to navigate. Reviewers independently inspect relevant code, failure paths and evidence. A focuses on correctness and regressions. B focuses on reliability, permissions, operability and maintenance. Both can flag a defect in any area.

## Reviews and corrections

Capture the input **before** dispatch with `engpipe task review-input SLUG design` or `engpipe task review-input SLUG code`. Preserve its `against` token. Give both reviewers the same task inputs and keep their findings separate until both return. If the input changes while the reviewers are working, discard stale answers. Do not obtain a new token on behalf of an old reviewer report.

An example individual report:

```json
{
  "verdict": "FIX",
  "blockers": 1,
  "findings": [{
    "severity": "BLOCKER",
    "location": "src/retry.py:42",
    "evidence": "The timeout path submits the same charge again",
    "impact": "Retry can charge a customer twice",
    "suggested_fix": "Reuse the original idempotency key"
  }],
  "checked": ["Inspected the retry branch and the duplicate-charge regression test"],
  "uncertainties": []
}
```

The orchestrator records actual reports through the CLI, supplies the original `--against` and concrete `--checked` evidence, and forwards actionable findings. Pass findings as a JSON array to `--findings`. Each entry follows the schema above. A PASS may include MINOR findings only. The blocker count equals the number of BLOCKER entries. Use the exact host model or session identity only when available. The default policy requires independent contexts. Under strict family routing, the author and both reviewers also need distinct declared families.

After a FIX, the implementer or architect addresses the finding. Submit the revised work to both reviewers. For a large design, the human approves the reviewed design before implementation. A terminal waiting for approval must preserve the task so the user can continue it.

## Recovery

The task slug and records in `.engineering-pipeline/tasks/SLUG/` identify the work. Keep these files local. Restore the request, review input, implementation diff, prior findings and the last completed phase before proceeding. Recheck stale evidence. Reusing a model conversation is optional. A local confirmation records an operator's decision within the trust boundary of the machine.

For a v1/v2 in-flight task, use `engpipe task migrate SLUG`. The migration archives the old records, preserves the original frozen tests and requires fresh scope, validation and reviews. Old records lack a trustworthy original Git tree. The migration therefore submits the entire current source tree for review, including changes already committed. It lists previously known paths now absent as `migration_missing_paths`. The reviewers must treat historical deletions without recoverable contents as uncertainties, and request evidence where this affects the decision. Supply `--host codex`, `--host claude` or `--host opencode` to adopt that host's policy. Inspect the saved migration instructions before resuming. When an approved large design must change during implementation, use `engpipe task reopen-design SLUG`, then obtain a new pair of design reviews and a new human approval.
