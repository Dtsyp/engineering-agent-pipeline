# Component checks and review evidence

Commit `.engineering-pipeline/checks.json` with the application. It determines which processes the pipeline runs in the application working directory. Review this manifest like a CI workflow. Keep `.engineering-pipeline/tasks/` private.

## Configure the checks

1. Give each component paths, its upstream component names in `depends_on`, and at least one meaningful `slice_checks` command. Include shared configuration and tests. If `frontend` consumes `api` and `api` consumes `domain`, a change to `domain` must run all three sets of checks. A change to `frontend` only needs its own checks.
2. Set `full_checks` to the actual release checks. A unit suite, type checker, build and integration suite are common choices. Each command is an argv array. Choose executable names available on the target OS and within the application's environment.
3. Use [the example manifest](../examples/checks.json) as a starting point for a Python backend and a frontend. Adapt the paths, package scripts and dependencies to the actual application. Test an upstream change and an unmapped change before adopting the map.
4. Agree on acceptance tests before implementation and include them in the configured checks. Freezing protects their file contents. The application test runner determines which assertions actually execute. After a slice run `engpipe task slice-check SLUG`. A failed check leaves the previous passing baseline in place. Resolve a coverage gap or an invalid dependency declaration before continuing.
5. Run `engpipe task gate SLUG` after the final slice. Dispatch both code reviewers on its recorded input, including the full check evidence. Changes to the code, the manifest or the evidence can invalidate a previously passing run.

## Gate reuse

By default the full gate executes its commands on each invocation. To allow reuse, explicitly configure `gate_cache.enabled` and identify every dependency file, config file and relevant environmental input.

```json
{
  "gate_cache": {
    "enabled": true,
    "dependency_files": ["requirements.lock"],
    "config_files": ["pyproject.toml"],
    "environment": [["python", "--version"], ["python", "-m", "pip", "freeze", "--all"]],
    "env_vars": ["APP_MODE", "CI"]
  }
}
```

If a database image, container, compiler or external service affects outcomes, include a stable identity probe or keep reuse off. Declare environment variables by name. The cache records hashes of selected values and probe output. Ordinary test logs are separate and can contain whatever the application prints.

Reusing one intact gate preserves its identity. A newly executed gate produces evidence for another review round. Log integrity and manifest checks apply in both modes. A locally controlled test suite establishes only what those tests exercise.

## Evaluating cost

A small and a large task take different routes. To evaluate a change in routing, repeat the same representative bug fixes, refactors and cross-service features. Compare accepted behavior and escaped defects first, then wall time, model invocations, token usage and repeated test runs. Record unavailable host metrics as unknown. The shipped model profiles are examples and contain no price or quality benchmark.
