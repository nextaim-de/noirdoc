# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Latency work on the masking glue: both hot loops between detection and
substitution were superlinear, and both ran on the caller's event loop.
Entity semantics are unchanged apart from the overlap fix below.

### Changed
- **Overlap resolution is O(n log n) instead of O(n²).**
  `EnsembleDetector._merge_entities` rescanned every already-accepted entity
  for every candidate. Candidates are processed in start order, so the
  accepted spans of any one type are pairwise disjoint and ordered and only
  the most recent one can overlap the next candidate; the rescan is now a
  per-type lookup and the sort dominates. The result is identical entity for
  entity — the previous implementation is kept as a test oracle and the two
  are compared on randomized inputs. Merging 1 000 entities drops from
  ~9.9 ms to ~0.19 ms.
- **Substitution rebuilds the text once instead of once per entity.**
  `PseudonymizationEngine.pseudonymize` replaced entities back to front,
  copying the whole string every time — O(entities × text length). It now
  emits the unchanged slices and the pseudonyms into one list and joins.
  Pseudonymizing a 200 KB text with 1 000 entities drops from ~7.1 ms to
  ~0.41 ms. Pseudonym numbering is unchanged: pseudonyms are still minted
  back to front, because the mapper's counters are order-sensitive and
  callers persist the mapping.
- **Large merges no longer block the event loop.** Above 64 entities
  `EnsembleDetector.detect` hands overlap resolution and PERSON validation to
  a worker thread; below that the thread hop would cost more than the work.
  In-process async callers (the Noirdoc Cloud gateway) keep serving other
  requests while a large document merges. `pseudonymize` stays synchronous —
  it is linear now.

### Fixed
- **Overlapping entities corrupted the pseudonymized output.** The detector
  ensemble deliberately keeps dual-type overlaps (a PERSON and a LOCATION span
  covering the same characters, `_merge_entities` rule 4). Substitution ran
  back to front, so the second replacement spliced its pseudonym into the
  already-substituted text at offsets that no longer meant what the caller
  meant. The output ended up with a half-eaten placeholder that no reverse
  mapping can undo, plus a pseudonym minted for a value that never reached the
  output. Substitution is now a single forward pass that clips overlaps: the
  span that starts first wins, and an entity starting inside the consumed span
  is skipped whole — including its mapping, so no phantom pseudonyms are
  created. Non-overlapping entities are unaffected, byte for byte and
  including the pseudonym numbering.

  **Caveat — read this before upgrading.** This trades corruption for a
  partial leak. Skipping the overlapping entity means the characters of the
  losing span that reach beyond the winner's end are now emitted **in
  cleartext**: with PERSON `[10, 20)` overlapping LOCATION `[15, 25)`, the
  five characters at `[20, 25)` — the tail of a span the detectors flagged as
  a LOCATION — appear verbatim in the output. Under the old back-to-front
  splice those characters were absent, because the (corrupt) LOCATION
  substitution had consumed them. So a dual-type overlap that previously
  produced unusable-but-fully-masked garbage can now leak part of the
  detected entity. A strictly better variant is possible and was deliberately
  **not** implemented here, because it goes beyond the reviewed scope of this
  change: keep the skip for the mapping decision, but pseudonymize the
  remainder slice `text[consumed_to:entity.end]` under its own mapping —
  full coverage, still reversible, still one pass.

## [0.1.2] — 2026-04-27

Security patch covering all High-severity findings from the 0.1.1
internal security review, plus an Excel-redaction quality fix that
restores parity between the CLI/SDK/daemon path and the noirdoc-cloud
proxy. Recommended upgrade for anyone running 0.1.x.

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

### Fixed
- **XLSX redaction quality regression.** `Redactor.redact_file` (used
  by `noirdoc redact`, the Python SDK, and the daemon) flattened every
  cell across every sheet into a single ` | `-joined string before
  detection, then did substring `cell.value.replace()` on
  reconstruction. Cell context was destroyed and many entities — short
  surnames, locations, numerically-typed cells — were silently missed.
  XLSX inputs now route through
  `noirdoc.file_analysis.xlsx_inference.pseudonymize_xlsx_smart`, which
  classifies columns by header keyword, samples the first rows for
  unclassified columns, and writes per-cell `<<TYPE_N>>` pseudonyms via
  `mapper.get_or_create()`. The reveal path was already cell-aware and
  round-trips correctly. This was the same path the noirdoc-cloud proxy
  has always used; the SDK simply wasn't wired up to it.

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
