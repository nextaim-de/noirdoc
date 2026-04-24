# Releasing noirdoc

How to cut a release. For background on why it's set up this way, see the
[release plan](https://github.com/nextaim-de/noirdoc/blob/main/.github/workflows/release.yml).

## Summary

- Tag `v0.1.0` → publishes to **PyPI** (via Trusted Publishing, requires approval in the `pypi` environment).
- Tag `v0.1.0rc1`, `v0.1.0a1`, `v0.1.0.dev1` → publishes to **TestPyPI** (no approval).
- Tag `v0.1.0.post1` → publishes to **PyPI** (post-release of a final).
- Any other tag shape starting with `v` → routed to TestPyPI as a safe default.
- Version is derived from the git tag via `hatch-vcs`. Do **not** hand-edit a version in `pyproject.toml`.

## One-time setup

### 1. Register pending Trusted Publishers

On **PyPI** → https://pypi.org/manage/account/publishing/ → *Add a pending publisher*:

| Field               | Value        |
|---------------------|--------------|
| PyPI project name   | `noirdoc`    |
| Owner               | `nextaim-de` |
| Repository          | `noirdoc`    |
| Workflow filename   | `release.yml`|
| Environment         | `pypi`       |

Repeat on **TestPyPI** → https://test.pypi.org/manage/account/publishing/ with environment `testpypi`.

A "pending" publisher reserves the name on each index before the first upload. The first successful publish promotes it to a regular publisher.

### 2. Create GitHub environments

Repo → Settings → Environments → *New environment*:

- `pypi` — enable **Required reviewers** and add the release managers. This is the approval gate for real publishes.
- `testpypi` — no protection. Rehearsals should not require a human in the loop.

### 3. Confirm workflow permissions

Repo → Settings → Actions → General → Workflow permissions: leave at *Read repository contents and packages permissions* (the default). The release workflow opts into `id-token: write`, `attestations: write`, and `contents: write` at the job level.

## Per-release checklist

1. **Update the changelog.** Move items under `## [Unreleased]` into a new `## [0.1.0] — YYYY-MM-DD` section. Keep an empty `## [Unreleased]` skeleton on top.
2. **Commit.** `git commit -am "chore(release): 0.1.0"`
3. **Tag.** `git tag -a v0.1.0 -m "v0.1.0"`
4. **Push.** `git push origin main --follow-tags`
5. **Watch the run.** GitHub → Actions → *Release*. For PyPI tags you will need to approve the deployment to the `pypi` environment.
6. **Smoke test.** In a clean venv:
   ```bash
   pip install "noirdoc==0.1.0"
   noirdoc --help
   python -c "import noirdoc; print(noirdoc.__version__)"
   ```

## First-release rehearsal

Strongly recommended for the very first publish, or after any change to the release workflow or packaging.

1. Tag `v0.1.0rc1` and push. This routes to TestPyPI.
2. Smoke test from TestPyPI (deps still live on real PyPI, hence `--extra-index-url`):
   ```bash
   python -m venv /tmp/noirdoc-rc && source /tmp/noirdoc-rc/bin/activate
   pip install --index-url https://test.pypi.org/simple/ \
               --extra-index-url https://pypi.org/simple \
               "noirdoc==0.1.0rc1"
   noirdoc --help
   ```
3. If clean, tag `v0.1.0` for the real publish.

## Troubleshooting

**Build step fails with `version 0.0.0` or `LocalVersionLabelError`.**
`hatch-vcs` can't see the tag. The workflow uses `fetch-depth: 0`; if you're building locally, make sure your checkout has tags (`git fetch --tags`).

**"Tag expects / Wheel reports" step fails.**
The tag didn't match `vX.Y.Z[...]` in a way `hatch-vcs` accepts. Valid: `v0.1.0`, `v0.1.0rc1`, `v0.1.0.dev3`, `v0.1.0.post1`. Invalid: `0.1.0` (missing `v`), `v0.1.0-beta` (hyphenated pre-release segments aren't PEP 440).

**Publish step fails with `Trusted publisher not configured`.**
The pending publisher on PyPI/TestPyPI doesn't match the triggering repo + workflow + environment. Re-check the four fields in the *pending publisher* form against what the workflow run shows.

**GitHub Release notes are empty.**
The changelog section `## [<version>]` wasn't present or didn't match exactly when the tag ran. Update `CHANGELOG.md` before tagging. The workflow falls back to a generic message if the section is missing; you can edit the release body after the fact.

**Deleting a bad release.**
PyPI does not allow re-uploading a file with the same filename. If a publish goes wrong, bump the version (e.g. `v0.1.0rc2`) rather than trying to reuse the name.

## Future hardening

- Pin all third-party actions to a full-commit SHA instead of tag/branch refs (`actions/checkout@v4` → `actions/checkout@<sha>`). Useful once the release cadence picks up.
- Add a scheduled `pip-audit` job against the published sdist.
- Add a smoke-install job that runs after publish: fetch `noirdoc==<tag>` from PyPI/TestPyPI and exercise the CLI.
