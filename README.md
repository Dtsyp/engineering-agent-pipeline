# Engineering Agent Pipeline

A portable **engineering workflow**, not a versioned migration guide. It coordinates specialized agents, architecture approval, targeted slice checks, two independent code reviews, and auditable completion gates. Suitable for team-maintained repositories. English-facing role prompts, no user-specific paths or credentials.

**Status:** tested Python core and OpenCode agent configuration on Linux; Claude Code / Codex role exports are adapters and **not equivalent automatic orchestration**. Full production E2E across hosts is not claimed.

## Guarantees and non-guarantees

- ✅ One accountable implementer per approved task/slice, with optional continuation of its conversation; missing or expired session ID never blocks task recovery.
- ✅ New tasks require preflight before Analyst and a durable evidence context before design/implementation. Downstream roles reuse that map without treating summaries as correctness proof.
- ✅ After **each implementation slice**, run `engpipe task slice-check <slug>`: selects checks for changed components **and transitive downstream consumers** using a project impact map. It does **not** run the full suite.
- ✅ After **all** slices, `engpipe task gate <slug>` obtains authoritative full-suite evidence; both code reviewers inspect the same resulting fingerprint. A prior PASS is reused only under an explicitly complete, unchanged code/dependency/config/environment fingerprint with an intact log. Otherwise the full suite runs.
- ✅ Large/high-risk work requires architecture, two independent design reviews, and interactive human approval. Small tasks skip design, never code review.
- ✅ Independent Review A/B may run concurrently in supported isolated host contexts; sequential fresh-context fallback remains mandatory when safe parallel dispatch is unavailable.
- ✅ Finishing fails on stale fingerprint, changed frozen tests, missing PASS, model-family collision, or missing human approval. Optional session metadata does not block progress.
- ⚠️ Models recorded in task state are declared configurations or operator-supplied metadata; the CLI does **not** cryptographically authenticate provider invocation. Inspect host logs before claiming live independence.
- ⚠️ Agent prompts are not security isolation. Use least-privilege credentials, CI, branch protection, and approvals for high-risk changes.
- ⚠️ `engpipe` **does not guarantee agents never stop**. It avoids spurious stops due to ephemeral metadata, but deliberately refuses to certify untested or unsafe work.

## Quick start

Requirements: Python 3.11+, Git, a POSIX-compatible terminal; for Windows use **WSL2**. An initial Git commit must exist in project repositories for fingerprints.

```bash
# clone your organization's copy of this repository
git clone <your-repository-url> engineering-agent-pipeline
cd engineering-agent-pipeline

python3 -m unittest discover -s tests -v
python3 tools/pipeline.py profile show max-quality
bash install.sh opencode
python3 tools/pipeline.py doctor
```

The `install.sh` wrapper accepts `opencode` (default), `claude`, or `codex`. `install.ps1` runs the installation inside WSL2. **Provider API credentials never go into this repository.** Install adapters by e.g. `bash install.sh claude` or `bash install.sh codex` (see [adapters](docs/ADAPTERS.md)). Add `~/.local/bin` to `PATH` if necessary.

Initialize a **separate target code repository**:

```bash
cd /path/to/your-product-repo
engpipe init
# edit .engineering-pipeline/checks.json — supply real checks and dependencies
engpipe task start add-checkout --kind feature --size large
```

Read [Team Installation Guide](docs/INSTALL.md), [Agent Role Contracts](docs/ROLE_CONTRACTS.md), and [Test Selection Guide](docs/TESTS.md).

## CLI commands

```bash
engpipe profile list
engpipe profile show max-quality
engpipe profile use balanced
engpipe model list
engpipe model set pipeline-implementer openai/gpt-6.1-sol
engpipe doctor

# In a project repo:
engpipe init
engpipe task start my-feature --kind feature --size small
engpipe task preflight my-feature --goal "observable outcome" \
  --in-scope "bounded behavior" --out-of-scope "explicit non-goal" \
  --constraint "no new dependencies" --acceptance "observable check" \
  --decision "fixed product behavior"
# Analyst returns source evidence; record reusable navigation facts:
engpipe task analysis-context my-feature \
  --evidence "src/service.py:42 | current handler owns the behavior" \
  --component "service + API consumer" --test "tests/test_service.py"
engpipe task freeze-tests my-feature tests/test_contract.py
engpipe task slice-check my-feature     # each slice; targeted + downstream
engpipe task gate my-feature            # runs or safely reuses authoritative evidence
engpipe task status my-feature
engpipe task finish my-feature          # only after two passing reviews
```

`model set` updates one agent. A conflicting model family produces a routing warning and prevents successful independent review until fixed; it does not destroy existing task state. Profiles are in `profiles/`, default **Max Quality**, with Balanced and Budget available. Change `model:` in agent YAML directly if preferred.

For tasks run via another host, use `engpipe task start ... --architect-model ... --implementer-model ...`, `engpipe task author-model ...` when the author changes, and review commands `--model ...` to record model actually selected by that host. Always attach evidence in external review records. A different host cannot automatically inherit OpenRouter authentication or access to arbitrary underlying model families.

## Layout

```text
agents/               canonical agent roles (OpenCode V2 compatible)
profiles/             max-quality / balanced / budget models
adapters/             host-specific exports and notes
bin/ocpipe            CLI launcher
examples/checks.json sample component/dependency mapping
install.sh            macOS, Linux, WSL2
install.ps1           Windows entrypoint through WSL2
tools/pipeline.py     task state, fingerprints, checks, reviews, profiles
AGENTS.md             shared engineering guidance
docs/ROLE_CONTRACTS.md  input/output, review, recovery and escalation contract
.github/workflows/    tests for this configuration repo
```

## Common troubleshooting

- **Old or missing session ID:** continue in a new session from `.engineering-pipeline/tasks/<slug>/` artifacts. Never invent IDs.
- **Missing API/model:** use another authorized model, record new author model and rerun relevant independent reviews. Different providers cannot be silently substituted.
- **Unmapped changed file:** update `checks.json` with component ownership and downstream dependencies; rerun slice-check. Never silently skip checks.
- **Code changed after full gate:** regenerate targeted evidence, full gate and both code reviews for the new fingerprint.
- **Gate unexpectedly reruns:** inspect `gate_cache` declarations and identity probes; reuse is deliberately disabled when any relevant context is missing, changed or unavailable.
- **GitHub sharing:** publish only this template/config repository. Project `.engineering-pipeline/tasks/` and secrets are local, not automatically synchronized between machines.

