# Pre-commit + CI setup for noirdoc-core

**Date:** 2026-04-23
**Status:** Approved
**Scope:** Establish baseline pre-commit hooks and GitHub Actions CI for the OSS `noirdoc` repo, mirroring `noirdoc-cloud` with OSS-specific additions.

## Motivation

`noirdoc-cloud` has a mature `.pre-commit-config.yaml` (5 repos, enforcing lint/format/type-check on every commit). The newly-extracted OSS `noirdoc-core` repo has matching `[tool.ruff]` / `[tool.mypy]` / `[tool.pytest]` config in `pyproject.toml` but no hooks installed and no CI. Because the OSS repo is public and will accept external contributions, it needs two things cloud does not:

1. **Secrets scanning** — a public repo cannot tolerate an accidentally-committed API key.
2. **Server-side enforcement** — contributors will not install pre-commit hooks locally; CI must run them.

## Non-goals

- Release workflow (PyPI publish on tag). Tracked in the extraction plan; separate concern.
- `pre-commit.ci` bot. Worth revisiting once external PRs start landing.
- Coverage reporting (codecov / coveralls).
- Dependabot / renovate.
- Changes to cloud's pre-commit config.

## Components

Two files, nothing more.

### 1. `.pre-commit-config.yaml`

Mirrors cloud with three intentional deltas:

| Change from cloud | Reason |
|---|---|
| `pyupgrade --py312-plus` (was `--py310-plus`) | OSS `requires-python = ">=3.12,<3.14"` |
| Drop `types-pyyaml` from mypy `additional_dependencies` | No yaml usage in the repo |
| Add gitleaks hook | Public repo — secrets scanning is table stakes |

Hook repo versions are pinned to match cloud exactly where overlap exists, so bumps can happen together.

```yaml
repos:
-   repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
    -   id: trailing-whitespace
    -   id: end-of-file-fixer
    -   id: check-yaml
    -   id: debug-statements
    -   id: name-tests-test
        args: [--pytest-test-first]
-   repo: https://github.com/asottile/add-trailing-comma
    rev: v4.0.0
    hooks:
    -   id: add-trailing-comma
-   repo: https://github.com/asottile/pyupgrade
    rev: v3.21.2
    hooks:
    -   id: pyupgrade
        args: [--py312-plus]
-   repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.19.1
    hooks:
    -   id: mypy
-   repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.2
    hooks:
    -   id: ruff-check
        args: [--fix]
    -   id: ruff-format
-   repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
    -   id: gitleaks
```

### 2. `.github/workflows/ci.yml`

Two jobs, both triggered on `push` to `main` and on all pull requests.

**Job `lint`:**
- Checkout.
- Setup Python 3.12.
- Install `pre-commit` via `pip`.
- Cache pre-commit environments keyed on `.pre-commit-config.yaml` hash.
- `pre-commit run --all-files`.

Target runtime: ~30s warm, ~2 min cold.

**Job `test`:**
- Matrix over Python 3.12 and 3.13.
- Checkout.
- Setup Python from matrix.
- Install Poetry (pin a version, e.g. 1.8.x).
- Cache `~/.cache/pypoetry` and `.venv`.
- `poetry install --with dev` — baseline install only, **no `[full]` extras**. Tests the default install path users hit with `pip install noirdoc`. Keeps CI under 5 minutes and avoids pulling torch / spaCy / GLiNER weights.
- `pytest -m "not slow"` — skips tests that load ML models (already marked via `[tool.pytest.ini_options] markers = ["slow: marks tests as slow (loading ML models)"]`).

Slow / ensemble tests stay out of default CI. A separate opt-in workflow (`full-tests.yml`) can be added later when the ML-heavy test path needs regression coverage.

## Rollout

On `main` directly (the repo is days old and has a single committer):

1. Add `.pre-commit-config.yaml`. Run `pre-commit run --all-files` once. Commit the config and any auto-fixes together.
2. Run `pre-commit install` locally to activate hooks on commit.
3. Add `.github/workflows/ci.yml`. Push. Verify both jobs green.

No feature flags, no staged rollout — this is dev tooling, not product.

## Verification

- `pre-commit run --all-files` exits 0 on a clean checkout.
- Intentionally committing a dummy `AWS_ACCESS_KEY_ID=AKIA…` string is rejected by gitleaks (smoke test, revert immediately).
- CI runs green on the commit that introduces `ci.yml`.
- CI lint job takes <1 min warm.
- CI test job completes under 5 min on both 3.12 and 3.13.

## Open items deferred to execution

- Exact Poetry pin version in CI (latest 1.8.x at implementation time).
- Whether to use `actions/setup-python` + `pip install pre-commit` or the purpose-built `pre-commit/action@v3` composite action. Both work; pick the simpler one during implementation.
- Exact gitleaks version — v8.21.2 is the plan; bump to whatever is current at implementation time.
