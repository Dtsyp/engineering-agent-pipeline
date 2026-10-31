# Installation for teams

## Requirements

- Python 3.11+, Git; shell commands in checks.json must exist in the **project runtime**.
- macOS/Linux: Terminal. Windows: WSL2 Ubuntu (recommended; Windows OpenCode Desktop can attach to a WSL server).
- Review your organization's code/data policy before uploading proprietary code to any external model provider. Create a private copy of the config repo if needed.

## 1. Install once per device

```bash
git clone <team-repo-url> engineering-agent-pipeline
cd engineering-agent-pipeline
python3 -m unittest discover -s tests -v
bash install.sh opencode   # or claude / codex; see ADAPTERS.md
```

Windows PowerShell from the cloned folder: `./install.ps1` (uses WSL2). On macOS/Linux no WSL is needed. OpenCode account/provider credentials must be connected **per machine** and are never copied from Git.

## 2. Configure model profile

```bash
engpipe profile list
engpipe profile use max-quality  # default; needs its models connected
engpipe model set pipeline-code-a openrouter/anthropic/claude-sonnet-5.5
engpipe doctor
```

Profiles: `max-quality`, `balanced`, `budget`. Models can always be changed individually in the agent `.md` file or with `model set`. When a model swap causes a review-family collision, the assignment is saved but **certification remains blocked** until models are made independent again.

## 3. Initialize each application repository

```bash
cd /path/to/application
engpipe init
# edit .engineering-pipeline/checks.json and set real commands
# make an initial local Git commit if repository has no HEAD
```

`.engineering-pipeline/` is local task state and is appended to `.git/info/exclude` by default; it does not go into your shared config repository. Use secure workspace-specific storage for any persistent task records. Review each application's `AGENTS.md` for domain-specific conventions.

## 4. Task workflow

```bash
engpipe task start add-feature --kind feature --size large
# Orchestrator records scope/constraints/decisions before invoking Analyst:
engpipe task preflight add-feature --goal "deliver accepted behavior" \
  --in-scope "named workflow" --out-of-scope "unrelated API changes" \
  --constraint "no new dependencies" --acceptance "contract test passes" \
  --decision "preserve existing compatibility"
# After Analyst, persist only verified navigation evidence:
engpipe task analysis-context add-feature \
  --evidence "src/workflow.py:40 | current entry point" \
  --component "workflow and downstream API" --test "tests/test_workflow.py"
# analyst and L2 architect provide design.md
engpipe task design-review add-feature a PASS 0 --model openrouter/moonshotai/kimi-k3.1-pro
engpipe task design-review add-feature b PASS 0 --model openrouter/z-ai/glm-5.3
engpipe task approve-design add-feature   # interactive human confirmation
engpipe task freeze-tests add-feature tests/test_accepted_contract.py
# implement slice 1
engpipe task slice-check add-feature
# implement slice 2
engpipe task slice-check add-feature
# ... only after all slices ...
engpipe task gate add-feature
engpipe task code-review add-feature a PASS 0
engpipe task code-review add-feature b PASS 0
engpipe task finish add-feature
```

**The `PASS` commands above illustrate CLI syntax, not permission to invent verdicts.** Call them only after two real independent reviews, with proof in host logs and findings. A second reviewer may reject work; relay FIX to Implementer, rerun targeted checks, full gate and both reviews after modifications. Freeze acceptance tests before implementation. With small tasks skip the architecture stage but still require both code reviews.

Review A/B are safe to dispatch concurrently only when the running host exposes isolated fresh child/background sessions and can collect both without sharing either report. OpenCode host capabilities vary by version/configuration; this repository does not enable experimental flags or assume a batch API. If safe parallel dispatch is absent or any launch fails, run A and B sequentially in fresh contexts and withhold A's report from B. Both verdicts, model-family separation, matching fingerprints and human design approval remain mandatory.

## 5. Recover after interruption, device change or missing session ID

Task identity = its slug and `.engineering-pipeline/tasks/<slug>/` state; session IDs are optional. On failure, open `engpipe task status <slug>`, rehydrate new agent from design/acceptance tests/diff/last findings and continue. Do not repeat approved work only because the conversation was lost. If model changes, `engpipe task author-model <slug> --role implementer --model ...` invalidates both code review records (similarly architect changes invalidate design approval). Do not copy private task state into public GitHub.

## 6. Safety and deployment limitations

- Keep API secrets, auth JSON, production data and task histories out of Git.
- Test this pipeline in a toy project first. Automated host-specific callbacks and runtime model attestation are not universal.
- Prior to team rollout run the unit tests, a successful E2E, and a negative E2E (failing test, stale fingerprint, rejected design, lost session, two conflicting models).
- Existing global OpenCode `AGENTS.md` is backed up but replaced by this repository's global guidance. Restore/merge existing organization-specific rules as needed.
- Exact environment paths and model access differ per host; the default profile is an example, not a guarantee of entitlements.

## 7. Update an existing installation

The OpenCode installation uses symlinks to this checkout, so an ordinary pull or applied patch updates role files and the CLI in place. Re-running the installer is optional but recommended after installer/layout changes because it refreshes links and creates a backup.

macOS or Linux:

```bash
cd /path/to/engineering-agent-pipeline
git pull --ff-only
python3 -m unittest discover -s tests -v
bash install.sh opencode
opencode reload
engpipe doctor
```

Windows with WSL2, from PowerShell in the repository folder:

```powershell
wsl.exe -e bash -lc 'cd /mnt/c/path/to/engineering-agent-pipeline && git pull --ff-only && python3 -m unittest discover -s tests -v'
./install.ps1 -Target opencode
wsl.exe -e bash -lc 'opencode reload && engpipe doctor'
```

Prefer opening a WSL shell and using the macOS/Linux commands when the repository path contains spaces or your distro needs a specific shell setup. If `opencode reload` cannot reach the running service, close and restart that OpenCode CLI/service, then run `engpipe doctor`. Reload/restart can interrupt active child sessions, so do it after their results are saved. Provider connections remain machine-local and are not changed by installation.
