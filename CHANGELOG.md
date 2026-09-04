# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **DOCX: everything outside paragraph runs was invisible to redaction.**
  `extract_docx` walked `doc.paragraphs` and `doc.tables` only, so PII in
  content controls (`w:sdt`), tables nested inside a cell, text boxes and
  shapes (`w:txbxContent`), tracked changes (`w:ins`/`w:del` — deleted text is
  still stored in the file), and the footnotes/endnotes parts was neither shown
  to the detector nor rewritten. One shared XML walker now drives extraction,
  redaction and reveal over all of them, so the surfaces the detector sees and
  the surfaces the rewrite touches cannot drift apart. Tracked deletions are
  stripped from the output entirely. Inline pictures, line breaks and tab stops
  survive the rewrite. ([#17])
- **DOCX: `docProps`, comment authors and `word/people.xml` survived
  redaction.** python-docx preserves every package part it does not edit, so a
  redacted document still carried creator, lastModifiedBy, title, subject,
  `app.xml` Manager/Company/HyperlinkBase/TitlesOfParts, custom properties,
  comment `w:author`/`w:initials`, and modern comment identities — verbatim.
  All of these are now pseudonymized through the shared mapper and are
  reversible. `docProps/thumbnail.jpeg` (a rendered preview of the *original*
  page) and `customXml/` data islands cannot be pseudonymized and are dropped
  on write; they are reported separately from the entity count, since carrying
  them says nothing about whether a document holds PII. ([#14])
- **DOCX: a redacted hyperlink still pointed at the real address.** The
  display text was rewritten, but the address itself lives in
  `word/_rels/*.rels` — one `unzip -p` away in a shipped document, and German
  business letters almost always carry a hyperlinked address in the footer.
  Every external `mailto:` relationship, in every part (document, headers,
  footers, comments, notes), is now mapped through the shared mapper and
  percent-encoded into the target (`mailto:%3C%3CEMAIL_1%3E%3E`, since `<` and
  `>` are illegal URI characters), so it reuses the placeholder the link text
  already got and reveals back to the original. Non-`mailto:` targets are left
  alone. ([#27])
- **DOCX: an entity inside a hyperlink was left in the link.** The rewrite read
  `para.text` (which includes hyperlink text) but wrote back through
  `para.runs` (which does not), so the placeholder was prepended while the
  original address stayed in the link run — and an unrelated hyperlink in the
  same paragraph was duplicated into the first run. Each hyperlink is now its
  own rewrite segment. The relationship *target* is still not scrubbed
  ([#27]). ([#16])
- **XLSX: charts, hyperlinks, filters, validations and pivot captions were
  round-tripped verbatim.** Excel writes a cache of the charted cells, so an
  Excel-authored chart kept rendering the original names after the sheet cells
  were pseudonymized. Now covered, in both directions: chart string caches and
  titles, hyperlink tooltip/display and `mailto:` targets (percent-encoded in
  the target, since `<` and `>` are illegal URI characters), AutoFilter
  criteria, conditional-formatting text criteria and string literals,
  data-validation inline lists and prompts, and pivot-table captions and label
  filters. Defined names and sheet titles remain uncovered — renaming either
  would require rewriting every formula that references them. ([#15])
- **XLSX: row 1 was never redacted, and single-row sheets were skipped.** The
  column tiers read row 1 as a header and the rewrite started at row 2, so a
  sheet whose data begins at row 1 — a pasted list, an export with no header —
  shipped its whole first record, and a sheet with only one row was skipped
  before classification ran. Both silently: the run still reported a healthy
  entity count. Row 1 is now re-examined after the columns are classified and
  redacted where it holds data. A cell whose text matches the header keyword
  map stays a label, and elsewhere the column has to have been classified from
  the rows below, so an unrecognized header over numbers is still left alone.
  Row-1 values also no longer end up as keys in the logged
  `column_classifications`. ([#30])
- **XLSX: a load failure returned the original workbook as "redacted".**
  `pseudonymize_xlsx_smart` swallowed zip-safety rejections, corrupt packages
  and openpyxl parse failures (pivot caches with manually grouped fields) and
  returned an empty result; the SDK then wrote the *original* file to the
  output path and reported `0 entities`, and the proxy forwarded it to the
  provider. It now raises `XlsxLoadError` and every caller fails closed. The
  DOCX parts pass fails closed the same way. ([#13])

### Changed
- Docs: all repo URLs now point at `noirdoc-ai/mask-engine` (was
  `nextaim-de/noirdoc`); `CONTRIBUTING.md` rewritten for the uv + Makefile
  workflow (was Poetry); `docs/RELEASING.md` notes that a repo move requires
  re-registering the Trusted Publisher on both indexes. ([#12])
- **The XLSX free-text NER pass is bounded in detect and block modes.** A
  workbook with thousands of distinct comments cost one detector call each.
  Detect and block modes now cap the scan and stop at the first hit, and report
  what they skipped in `free_texts_skipped` rather than skipping silently.
  Redact mode is never capped — a partial redact pass would forward unmasked
  text. Mirrored values the mapper already knows (chart caches, filter
  criteria) skip the detector entirely. ([#11])

### Fixed
- **Plain-text fallback is now visible.** When DOCX/XLSX reconstruction fails
  (or the format never supported it, e.g. PDF), `noirdoc redact` no longer
  writes masked UTF-8 text into an explicit non-`.txt` `-o` target: the output
  is redirected to `<stem>.txt` and a warning naming both paths (plus the
  reason) is printed on stderr — on the in-process and the daemon path alike.
  `RedactionResult` and the daemon protocol's `RedactResult` gained a
  `reason: str | None` field explaining why the original format was dropped
  (`reconstructed=False`, `mime_type="text/plain"` remain the machine
  signals). ([#10])

[#10]: https://github.com/noirdoc-ai/mask-engine/issues/10
[#11]: https://github.com/noirdoc-ai/mask-engine/issues/11
[#12]: https://github.com/noirdoc-ai/mask-engine/issues/12
[#13]: https://github.com/noirdoc-ai/mask-engine/issues/13
[#14]: https://github.com/noirdoc-ai/mask-engine/issues/14
[#15]: https://github.com/noirdoc-ai/mask-engine/issues/15
[#16]: https://github.com/noirdoc-ai/mask-engine/issues/16
[#17]: https://github.com/noirdoc-ai/mask-engine/issues/17
[#27]: https://github.com/noirdoc-ai/mask-engine/issues/27
[#30]: https://github.com/noirdoc-ai/mask-engine/issues/30

## [0.1.3] — 2026-09-01

Security patch: XLSX redaction now covers everything outside the cell grid —
document properties, comments, headers/footers and pivot caches — reversibly,
and no longer crashes on chart sheets. Recommended upgrade for anyone
redacting spreadsheets.

### Security
- **XLSX metadata / comment / pivot-cache leak.** XLSX redaction only rewrote
  spreadsheet cells; the openpyxl round-trip preserved everything else
  verbatim — `docProps/core.xml` (creator, lastModifiedBy, title, subject,
  description, keywords, category), `docProps/custom.xml`, cell comments
  (author and text), sheet headers/footers, and pivot caches. A pivot cache is
  a snapshot of the source rows, so Excel kept displaying the original names in
  the pivot after the cells had been pseudonymized. All of these now go through
  the same mapper as cells (author-style fields are always PERSON/EMAIL,
  free-text fields through the detector, pivot fields classified like sheet
  columns), so `noirdoc reveal` round-trips them. Threaded comments,
  `xl/persons` and `app.xml` Manager/Company cannot be modelled by openpyxl and
  are dropped on write; they are counted but not reversible. Two behaviour
  changes follow: a workbook whose only PII was metadata used to be returned
  byte-identical and is now rewritten, and detect/block modes count document
  metadata — block-on-PII therefore trips on virtually every Excel-authored
  workbook, since they all carry a creator. A workbook without a `dc:creator`
  element is *not* reported with openpyxl's default `"openpyxl"` creator, and
  Excel's synthetic `tc={GUID}` author on threaded-comment mirrors is skipped.
  Classification keys never contain file-provided names (they are logged).

### Fixed
- **XLSX chart sheets.** A workbook containing a chart sheet crashed both
  redact and reveal (`AttributeError: 'Chartsheet' object has no attribute
  'max_row'`); chart sheets are now skipped by the cell pass and their
  headers/footers scrubbed like worksheet headers.

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

[#1]: https://github.com/noirdoc-ai/mask-engine/issues/1
[#2]: https://github.com/noirdoc-ai/mask-engine/pull/2
[#3]: https://github.com/noirdoc-ai/mask-engine/pull/3

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

[Unreleased]: https://github.com/noirdoc-ai/mask-engine/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/noirdoc-ai/mask-engine/releases/tag/v0.1.3
[0.1.2]: https://github.com/noirdoc-ai/mask-engine/releases/tag/v0.1.2
[0.1.1]: https://github.com/noirdoc-ai/mask-engine/releases/tag/v0.1.1
[0.1.0]: https://github.com/noirdoc-ai/mask-engine/releases/tag/v0.1.0
