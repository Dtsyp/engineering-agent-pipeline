# Targeted checks after slices; full checks at completion

The per-project `.engineering-pipeline/checks.json` is a **dependency-aware validation manifest**. It is not an agent prompt. It controls which commands are executed.

1. For each changed file since the last passing `slice-check`, resolve its owning component by matching path globs.
2. For every impacted component, recursively include components that list it in `depends_on` (downstream consumers). Run each unique `slice_checks` command once, passing executable+arguments as JSON arrays (no shell interpolation).
3. If any path lacks an owner, **do not claim slice PASS**. Extend the map with the affected component and dependency information. Unknown files are not a reason to run a full suite automatically, nor permission to skip tests.
4. A successful slice-check records a fingerprint, changed paths, impacted components, command list and a log. Failures preserve the old baseline so the next attempt checks the changes again.
5. The **single final full gate** runs `full_checks` after all slices, and only if there are no unverified changes since the last slice check. It must include the actual project-required test/typecheck/lint/build/integration steps. Review A and B run on the same fingerprint after the final gate.
6. After a review FIX produces code changes, another targeted slice-check is required, followed by a fresh final full gate and **both** reviewers. This is not redundant checking; the old evidence describes different code.

Example for Python backend + API + frontend:

```json
{
  "components": {
    "backend-domain": {
      "paths": ["backend/domain/**", "backend/tests/domain/**"],
      "depends_on": [],
      "slice_checks": [["python3", "-m", "pytest", "backend/tests/domain", "-q"]]
    },
    "backend-api": {
      "paths": ["backend/api/**", "backend/tests/api/**"],
      "depends_on": ["backend-domain"],
      "slice_checks": [["python3", "-m", "pytest", "backend/tests/api", "-q"]]
    },
    "frontend": {
      "paths": ["frontend/**"],
      "depends_on": ["backend-api"],
      "slice_checks": [["npm", "--prefix", "frontend", "test", "--", "--runInBand"]]
    },
    "project-meta": {
      "paths": ["README.md", ".gitignore", "pyproject.toml", "package.json"],
      "depends_on": [],
      "slice_checks": [["python3", "-c", "print('Check configuration mapping for your project')"]]
    }
  },
  "full_checks": [
    ["python3", "-m", "pytest", "backend/tests", "-q"],
    ["npm", "--prefix", "frontend", "run", "typecheck"],
    ["npm", "--prefix", "frontend", "run", "lint"],
    ["npm", "--prefix", "frontend", "test"],
    ["npm", "--prefix", "frontend", "run", "build"]
  ]
}
```

**Replace this example with real commands.** The `project-meta` example is *not* adequate validation for changes to build configuration; configure the appropriate checks in production. `engpipe init` creates an empty manifest that fails until configured. For monorepos add generated-code contracts and test impact of schema or OpenAPI changes. Cache successful tool outputs with your build system; the manifest itself does not implement incremental build caching.

**Assumptions:** impact graph is manually curated. It does not derive dependencies from the AST. The correctness of impacted-tests selection depends on this graph being maintained. CI and code owners should review changes to it.
