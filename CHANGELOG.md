# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] — 2026-04-27

### Added
- Daemon mode: an auto-spawned `noirdoc-daemon` keeps the spaCy + GLiNER
  models in memory across CLI invocations, eliminating the ~10s cold start
  per `noirdoc redact` call (~40× faster on warm calls). Communicates over
  an `AF_UNIX` socket under `~/.noirdoc/` with a Pydantic-validated
  JSON-lines protocol. Idle-shuts-down after 10 minutes
  (`NOIRDOC_DAEMON_IDLE_SECONDS`); version-mismatched daemons are
  respawned automatically; any daemon failure falls back transparently
  to in-process redaction. Opt out with `--no-daemon` or
  `NOIRDOC_NO_DAEMON=1`. New `noirdoc daemon {status,stop,restart,logs}`
  subcommands. ([#3])
- `noirdoc ns summary <ns>` — counts-only namespace inspection
  (`total_entities`, per-label `by_type`). Safe to capture in wrapper
  transcripts and audit logs; original values never appear in the output.
  Companion to `ns show`. ([#1], [#2])

[#1]: https://github.com/nextaim-de/noirdoc/issues/1
[#2]: https://github.com/nextaim-de/noirdoc/pull/2
[#3]: https://github.com/nextaim-de/noirdoc/pull/3

## [0.1.0] — 2026-04-24

First public alpha on PyPI.

### Added
- Initial scaffold: pyproject, MIT LICENSE, README, package skeleton.
- Detection ensemble (Presidio + optional GLiNER + Flair) extracted from the
  Noirdoc Cloud reverse proxy.
- File extractors for PDF (with optional OCR fallback), DOCX, XLSX, images, and
  plain text formats (TXT/CSV/MD/HTML).
- Reversible pseudonymization: `PseudonymMapper` with `to_dict`/`from_dict`
  serialization, `PseudonymizationEngine`, `ReidentificationEngine`, and
  `file_reidentification.service.reidentify_file_bytes` (DOCX/XLSX/plaintext
  roundtrip; PDF/PPTX/images return `None`).
- `MappingStore` with a pluggable `MappingBackend` protocol; concrete
  `MemoryMappingBackend`, `FileMappingBackend`, and `RedisMappingBackend`
  (via `noirdoc[redis]`).
- Persistent namespaces under `~/.noirdoc/namespaces/<name>/` with a per-
  namespace Fernet key (`0600`).
- High-level SDK: `noirdoc.redact(...)` and `noirdoc.Redactor(namespace=...)`.
- `noirdoc` CLI (Click) with `redact`, `reveal`, `lookup`, `ns {list,show,delete}`,
  and `models pull` subcommands.
- Project URLs in `pyproject.toml` (Repository, Issues, Changelog) for PyPI sidebar.

### Fixed
- Baseline install (`pip install noirdoc`) crashed with `ModuleNotFoundError:
  gliner` because the default `detector="ensemble"` eagerly imported GLiNER.
  The ensemble now detects an absent GLiNER, falls back to Presidio-only with
  a `UserWarning`, and keeps working. Explicit `--detector gliner` still fails
  loudly when the `[full]` extra isn't installed.

[Unreleased]: https://github.com/nextaim-de/noirdoc/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/nextaim-de/noirdoc/releases/tag/v0.1.1
[0.1.0]: https://github.com/nextaim-de/noirdoc/releases/tag/v0.1.0
