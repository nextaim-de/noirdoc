# Contributing to noirdoc

Thanks for your interest. Noirdoc is early — API will change — but contributions are welcome, especially around detectors, formats, and German-language coverage.

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh` if you don't have it.
- ~1 GB free disk for the `full` extra (GLiNER + Flair weights).

## Dev setup

```bash
git clone https://github.com/noirdoc-ai/mask-engine.git
cd mask-engine
make install                                     # uv sync --extra dev
uv run python -m spacy download de_core_news_lg
uv run pre-commit install
```

uv manages the local environment from the committed `uv.lock`; `make install` creates `.venv/` with the `dev` extra. For work on the ensemble detectors, add the ML extras: `uv sync --extra dev --extra full --extra redis`. The build backend is `hatchling`; the published wheel is built from `[project]` metadata. CI uses `astral-sh/setup-uv` with the same `uv sync --extra dev`.

## Running tests

```bash
make test               # fast tier, -m "not slow" (same as CI)
make test-slow          # model-loading tests (needs the full extra)
uv run pytest           # everything, unfiltered
```

Prefix one-off commands with `uv run`, or activate `.venv/` if you'd rather not.

## Lint and formatting

`pre-commit` runs ruff (check + format), mypy, pyupgrade, gitleaks, and a few hygiene hooks. CI enforces the same config.

```bash
make check              # fmt-check + lint + typecheck + test — mirrors CI
make fmt                # auto-format
uv run pre-commit run --all-files
```

Run `make help` for the full target list.

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

Open an issue at <https://github.com/noirdoc-ai/mask-engine/issues>. Include Python version, OS, the command or code that triggered the bug, and the traceback if any. A minimal reproducer helps a lot.

For security-sensitive bugs, see [SECURITY.md](SECURITY.md) — do not open a public issue.
