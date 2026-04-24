# Contributing to noirdoc

Thanks for your interest. Noirdoc is early — API will change — but contributions are welcome, especially around detectors, formats, and German-language coverage.

## Prerequisites

- Python 3.12 or 3.13
- [Poetry](https://python-poetry.org/) 2.0 or later — `pipx install poetry` if you don't have it.
- ~1 GB free disk for the `full` extra (GLiNER + Flair weights).

## Dev setup

```bash
git clone https://github.com/nextaim-de/noirdoc.git
cd noirdoc
poetry env use 3.12                              # or 3.13
poetry install --all-extras                      # dev + full + redis
poetry run python -m spacy download de_core_news_lg
poetry run pre-commit install
```

Poetry manages the local environment and resolves dependencies fresh from `pyproject.toml` on each checkout — noirdoc is a library, so the lockfile is not tracked (downstream consumers re-resolve). The build backend is `hatchling`; the published wheel is built from `[project]` metadata. CI uses plain `pip install -e ".[dev]"` for speed.

## Running tests

```bash
poetry run pytest                 # fast tier (CI default)
poetry run pytest -m slow         # includes model-loading tests
poetry run pytest -m "not slow"   # same as CI
```

Prefer `poetry shell` if you'd rather not prefix every command.

## Lint and formatting

`pre-commit` runs ruff (check + format), mypy, pyupgrade, gitleaks, and a few hygiene hooks. CI enforces the same config.

```bash
poetry run pre-commit run --all-files
```

## Adding a detector

Custom Presidio recognizers live in `src/noirdoc/detection/presidio_detector.py`. The German set — `GermanPhoneRecognizer`, `GermanSVNRRecognizer`, `GermanSteuerIDRecognizer`, `InvertedNameRecognizer` — is the reference pattern. Each one sets `supported_language="de"`; multi-language detectors register twice, once per language (see the `InvertedNameRecognizer` registration in the same file).

Tests go in `tests/test_presidio_detector.py`. Add both positive and negative cases, and cover the German edge cases you're designing for — lowercase terms, IBAN formatting, Steuer-ID checksums.

## Pull requests

- Keep PRs small and focused. One detector per PR beats a mega-PR.
- Every PR includes a test.
- Update `CHANGELOG.md` under `## [Unreleased]` with a one-line entry in the appropriate subsection (`### Added`, `### Changed`, `### Fixed`).
- CI must be green before review.

## Releasing

Only maintainers cut releases. The flow is tag-driven and uses PyPI Trusted Publishing — see [docs/RELEASING.md](docs/RELEASING.md) for the per-release checklist, the first-release rehearsal, and the one-time PyPI setup.

## Reporting bugs

Open an issue at <https://github.com/nextaim-de/noirdoc/issues>. Include Python version, OS, the command or code that triggered the bug, and the traceback if any. A minimal reproducer helps a lot.

For security-sensitive bugs, see [SECURITY.md](SECURITY.md) — do not open a public issue.
