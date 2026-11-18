# Engineering pipeline

Use the local `engpipe` CLI for task state and validation. The canonical role prompts live in `agents/`. See [role contracts](docs/ROLE_CONTRACTS.md), [host entry points](docs/ADAPTERS.md) and [terminal execution](docs/RUN.md).

1. Record the observable goal, scope, constraints, acceptance criteria and material decisions at preflight. Stop on a product question that changes correctness. Record source-based analysis before design or implementation.
2. Choose the smallest workflow consistent with risk. For large changes select one architect level, obtain separate A and B design reviews on the same review input, and seek explicit human approval of the reviewed design.
3. Freeze agreed acceptance tests. Assign one accountable implementer per slice. Run targeted checks for every changed component and its transitive downstream consumers, then run the configured full checks after the last slice.
4. Before each review pair, capture `engpipe task review-input SLUG design` or `engpipe task review-input SLUG code`. Give A and B identical inputs in fresh contexts. Collect both reports before sharing findings. Run them concurrently when the host supports isolated dispatch, otherwise use separate sequential invocations.
5. Record real verdicts against the original token with concrete evidence of what each reviewer checked. Follow the task's model policy. Configured model selectors and optional session IDs describe provenance; only host evidence can substantiate actual invocation details.
6. Return actionable FIX findings to the accountable author. Recheck changed code or design and obtain two fresh reviews for the new input. Only report completion after `engpipe task finish` succeeds.

Session expiration allows recovery from on-disk artifacts. Missing tests, stale evidence and outstanding human approval require action. Keep project task histories, credentials and model-provider secrets private. Commit and push when the user authorizes publication.
