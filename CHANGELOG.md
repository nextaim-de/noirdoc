# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] — 2026-04-27

Security patch covering all High-severity findings from the 0.1.1
internal security review. Recommended upgrade for anyone running
0.1.x.

### Security
- **PDF metadata leak.** PII embedded in a PDF's `/Info` dictionary
  (Author, Title, Subject, Creator, Producer, Keywords) was passed
  through unchanged. Metadata fields are now extracted with the page
  text so the detector ensemble pseudonymizes them before output.
- **DOCX header / footer / comment leak.** `extract_docx` and
  `_reconstruct_docx` only walked paragraphs and table cells — section
  headers, footers (default + first-page + even-page), and review
  comments survived untouched. All three surfaces are now extracted
  on input and rewritten on output.
- **OOXML zip-bomb defense.** DOCX and XLSX inputs are now pre-flighted
  through a zip-envelope check that refuses archives declaring more
  than 200 MB uncompressed or a compression ratio above 100×.
- **XML entity expansion.** `defusedxml` is now a baseline dependency
  so openpyxl's `iterparse` path is entity-safe by default. python-docx
  was already pinned to a `resolve_entities=False` parser.
- **Image decompression-bomb DoS.** OCR extraction now caps
  `Image.MAX_IMAGE_PIXELS` at 50 megapixels and converts
  `DecompressionBombWarning` into a hard refusal. The cap is
  scoped per-call so it cannot leak into other PIL consumers.
- **Detector ensemble silent failure.** A failing detector inside
  `EnsembleDetector` no longer produces an empty result silently;
  the failure is logged with the detector name so observability
  catches partial-coverage regressions.
- **Daemon protocol size limits.** `asyncio.start_unix_server` and
  the matching client now cap line buffers at 32 MB, and the
  protocol enforces per-field length caps (16 MB text, 4 KB paths,
  64 chars for namespace names).
- **Daemon path-trust check.** `handle_redact` now refuses input
  files and output directories that are not owned by the current
  UID. A peer cannot ask the daemon to read or overwrite another
  user's files.
- **Namespace name validation.** Namespace names are restricted to
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, blocking path traversal
  (`../`, `/`, leading dot) into or out of the namespace store.
- **Fernet key TOCTOU.** Per-namespace key creation now uses
  `O_CREAT | O_EXCL` with `0600`, the namespace directory is
  created with `0700`, and concurrent first-load races no longer
  clobber an existing key.
- **CLI output-path traversal.** `noirdoc redact -o / --output-dir`
  is now guarded against crafted input paths that resolved outside
  the chosen output directory, and refuses to write into the
  namespace store.
- **`ns show` requires `--unsafe`.** Printing a namespace's full
  pseudonym ↔ original mapping reveals every original value. The
  command now exits with an error and points at `noirdoc ns
  summary` unless `--unsafe` is passed.

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

[Unreleased]: https://github.com/nextaim-de/noirdoc/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/nextaim-de/noirdoc/releases/tag/v0.1.2
[0.1.1]: https://github.com/nextaim-de/noirdoc/releases/tag/v0.1.1
[0.1.0]: https://github.com/nextaim-de/noirdoc/releases/tag/v0.1.0
