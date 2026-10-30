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

## 5. Recover after interruption, device change or missing session ID

Task identity = its slug and `.engineering-pipeline/tasks/<slug>/` state; session IDs are optional. On failure, open `engpipe task status <slug>`, rehydrate new agent from design/acceptance tests/diff/last findings and continue. Do not repeat approved work only because the conversation was lost. If model changes, `engpipe task author-model <slug> --role implementer --model ...` invalidates both code review records (similarly architect changes invalidate design approval). Do not copy private task state into public GitHub.

## 6. Safety and deployment limitations

- Keep API secrets, auth JSON, production data and task histories out of Git.
- Test this pipeline in a toy project first. Automated host-specific callbacks and runtime model attestation are not universal.
- Prior to team rollout run the unit tests, a successful E2E, and a negative E2E (failing test, stale fingerprint, rejected design, lost session, two conflicting models).
- Existing global OpenCode `AGENTS.md` is backed up but replaced by this repository's global guidance. Restore/merge existing organization-specific rules as needed.
- Exact environment paths and model access differ per host; the default profile is an example, not a guarantee of entitlements.
