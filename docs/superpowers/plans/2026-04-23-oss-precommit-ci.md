# Pre-commit + CI for noirdoc-core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install pre-commit hooks matching `noirdoc-cloud` (with py312 + gitleaks additions) and add a GitHub Actions CI workflow that enforces them on every PR/push, plus a test matrix across Python 3.12/3.13.

**Architecture:** Two new files in `noirdoc-core`: `.pre-commit-config.yaml` and `.github/workflows/ci.yml`. Hooks cover whitespace, pyupgrade, mypy, ruff check+format, trailing commas, and gitleaks (secrets scan). CI has two jobs: `lint` (runs `pre-commit run --all-files`) and `test` (matrix over 3.12/3.13, installs baseline deps via `pip install -e ".[dev]"`, runs `pytest -m "not slow"`).

**Tech Stack:** pre-commit, GitHub Actions, `actions/setup-python`, ruff, mypy, gitleaks, pytest.

**Working directory for all tasks:** `/Users/maioloa/Documents/nextaim/noirdoc-core`

---

## File Structure

- **Create:** `.pre-commit-config.yaml` — hook pipeline; single source of truth for local + CI lint rules.
- **Create:** `.github/workflows/ci.yml` — GitHub Actions workflow with `lint` and `test` jobs.

Two tasks total. Task 1 lands pre-commit (local enforcement). Task 2 layers CI on top (server-side enforcement).

---

## Task 1: Add pre-commit config and auto-fix the repo

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Create `.pre-commit-config.yaml`**

Exact file contents — indentation matches cloud's style (mix of 4-space and 2-space blocks, preserved intentionally for diff parity):

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
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.15.2
  hooks:
    - id: ruff-check
      args: [--fix]
    - id: ruff-format
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.21.2
  hooks:
    - id: gitleaks
```

- [ ] **Step 2: Install pre-commit and enable the hook**

Run:

```bash
pip install --user pre-commit
pre-commit install
```

Expected: `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 3: Run all hooks across the repo once**

Run:

```bash
pre-commit run --all-files
```

Expected: first run downloads hook environments (takes 1–3 min). Some hooks may modify files (trailing whitespace, EOF, add-trailing-comma, ruff-format, ruff --fix). That's expected — re-run to confirm a clean pass.

- [ ] **Step 4: Re-run to verify idempotent clean state**

Run:

```bash
pre-commit run --all-files
```

Expected: every hook reports `Passed` (or `Skipped` for `check-yaml` if no YAML files staged). No `Failed` rows, no `files were modified by this hook` warnings.

- [ ] **Step 5: Smoke-test gitleaks**

Create a throwaway file to verify secrets scanning is wired up. This is a verification step — do NOT commit the file.

Create `/tmp/noirdoc_gitleaks_smoke.py` with body:

```python
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
```

Run:

```bash
pre-commit run gitleaks --files /tmp/noirdoc_gitleaks_smoke.py
```

Expected: `gitleaks` hook reports `Failed` and prints a finding mentioning `aws-access-token` or similar. Delete `/tmp/noirdoc_gitleaks_smoke.py` after verifying.

- [ ] **Step 6: Commit**

```bash
git add .pre-commit-config.yaml
# Include any files auto-modified by hooks in Step 3:
git add -u
git -c commit.gpgsign=false commit -m "chore: add pre-commit config mirroring noirdoc-cloud

- Hooks: trailing-whitespace, end-of-file-fixer, check-yaml,
  debug-statements, name-tests-test, add-trailing-comma,
  pyupgrade --py312-plus, mypy, ruff-check --fix, ruff-format, gitleaks.
- Versions pinned to match cloud repo where they overlap.
- pyupgrade targets py312 (repo requires >=3.12).
- gitleaks added for OSS secrets scanning."
```

Expected: hooks run on the commit itself (via `pre-commit install`) and the commit succeeds. If hooks modify anything during the commit, the commit is rejected — re-stage and retry with the same message.

---

## Task 2: Add GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the CI workflow**

Create `.github/workflows/ci.yml` with this content:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint (pre-commit)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: pre-commit/action@v3.0.1

  test:
    name: Tests (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"
      - name: Install baseline + dev deps
        run: pip install -e ".[dev]"
      - name: Download spaCy model
        run: python -m spacy download de_core_news_lg
      - name: Run tests
        run: pytest -m "not slow" -v
```

Notes on choices:
- `pre-commit/action@v3.0.1` handles its own cache keyed on the config file — no manual cache config needed.
- `cache: "pip"` on `actions/setup-python` caches the pip wheel download cache across runs.
- `fail-fast: false` so a 3.13-only failure doesn't hide a 3.12 regression and vice versa.
- Baseline install (`[dev]` only, not `[full]`) — mirrors what `pip install noirdoc` gets most users.
- `de_core_news_lg` matches `src/noirdoc/detection/presidio_detector.py`'s default; download step is cheap insurance in case tests import the model path.
- `concurrency` block cancels in-progress runs when a new commit lands on the same ref, saving CI minutes.

- [ ] **Step 2: Verify the YAML is valid**

Run:

```bash
pre-commit run check-yaml --files .github/workflows/ci.yml
```

Expected: `check-yaml` reports `Passed`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git -c commit.gpgsign=false commit -m "ci: add GitHub Actions workflow (lint + tests)

- lint: runs 'pre-commit run --all-files' via pre-commit/action.
- test: matrix over Python 3.12 and 3.13, baseline dev install,
  spaCy de_core_news_lg model, pytest -m 'not slow'.
- Concurrency group cancels superseded runs on the same ref."
```

Expected: pre-commit hooks pass on the commit, commit succeeds.

- [ ] **Step 4: Manual verification on first push**

When the repo is eventually pushed to GitHub (out of scope for this plan — do NOT push as part of execution; user will push manually), check that both jobs run and go green. If either fails, the fix belongs in a follow-up commit on `main`.

Specifically, watch for:
- `lint` job completes in <2 min warm / <3 min cold.
- `test` job completes in <5 min per Python version.
- If mypy complains about a file that passes locally, investigate env drift between local and CI (most likely missing stub packages).
- If gitleaks complains about anything in the committed tree, investigate — there should be no real secrets committed.

---

## Self-Review

**Spec coverage:**
- `.pre-commit-config.yaml` with three deltas (py312, drop types-pyyaml, gitleaks) → Task 1 Step 1.
- CI workflow with lint + test jobs → Task 2 Step 1.
- Python matrix 3.12 + 3.13 → Task 2 Step 1 (`matrix.python-version`).
- Baseline `[dev]` install, no `[full]` → Task 2 Step 1.
- Skip slow tests → Task 2 Step 1 (`pytest -m "not slow"`).
- Verification items from spec (pre-commit clean, gitleaks smoke test) → Task 1 Steps 4 & 5.
- Hook versions match cloud → Task 1 Step 1 literal YAML.

No spec requirement is missing from a task.

**Placeholders:** None. Every code block is literal and copy-pasteable. No "TBD", no "similar to above", no handwaving.

**Type / name consistency:** Workflow job names (`lint`, `test`), step names, and matrix key (`python-version`) are used consistently across the single YAML file. Hook versions in Task 1 match cloud's versions and are referenced only once.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-oss-precommit-ci.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
