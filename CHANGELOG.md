# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Latency work on the masking glue: both hot loops between detection and
substitution were superlinear, and both ran on the caller's event loop.
Entity semantics are unchanged apart from the overlap fix below.

### Added
- **`PseudonymizationEngine.pseudonymize_detailed`** returns the pseudonymized
  text *and* one `EmittedSpan` per placeholder written — the original text it
  replaced, the placeholder, and its position in the output. Callers that have
  to repeat the substitution somewhere the extracted text cannot reach (DOCX
  runs, XLSX cells) previously re-derived that pairing from entity offsets,
  which is a second implementation of the overlap rules. `pseudonymize()` is
  unchanged and returns just the text.

### Changed
- **Overlap resolution is O(n log n) instead of O(n²).**
  `EnsembleDetector._merge_entities` rescanned every already-accepted entity
  for every candidate. Candidates are processed in start order, so the
  accepted spans of any one type are pairwise disjoint and ordered and only
  the most recent one can overlap the next candidate; the rescan is now a
  per-type lookup and the sort dominates. The result is identical entity for
  entity — the previous implementation is kept as a test oracle and the two
  are compared on randomized inputs. Merging 1 000 entities drops from
  ~9.7 ms to ~0.18 ms.
- **Substitution rebuilds the text once instead of once per entity.**
  `PseudonymizationEngine.pseudonymize` replaced entities back to front,
  copying the whole string every time — O(entities × text length). It now
  emits the unchanged slices and the pseudonyms into one list and joins.
  Pseudonym numbering is unchanged: pseudonyms are still minted back to
  front, because the mapper's counters are order-sensitive and callers
  persist the mapping. Measured with `benchmark/bench_hot_loops.py`
  (best of five, against the 0.1.2 implementation):

  | entities | text | 0.1.2 | now |
  | ---: | ---: | ---: | ---: |
  | 10 | 128 B | 0.0039 ms | 0.0086 ms |
  | 100 | 1.2 KB | 0.049 ms | 0.079 ms |
  | 1 000 | 12 KB | 0.86 ms | 0.80 ms |
  | 1 000 | 200 KB | 7.1 ms | 0.82 ms |

  The win is the long-text row — 8.7× where it used to hurt. On short texts
  with few entities the new path is slower, because it does per-entity
  bookkeeping (overlap resolution and the emitted-span record) that the old
  string-splice did not: about +5 µs at ten entities and +30 µs at a hundred.
  The crossover is around a thousand entities, and from there — or on any
  large text — the new code wins. Either way the difference is negligible
  next to the model inference in the same request.
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
  output. Substitution is now a single forward pass that masks overlaps in
  pieces: the span that starts first wins and is replaced whole, and an entity
  reaching beyond it contributes its remainder — the slice
  `text[consumed_to:entity.end]` — which is replaced by its own pseudonym
  under its own entity type. An entity that lies entirely inside the consumed
  span has no remainder and is skipped whole, including its mapping, so no
  phantom pseudonyms are created. Non-overlapping entities are unaffected,
  byte for byte and including the pseudonym numbering.

  A remainder is trimmed before it is minted: leading and trailing whitespace
  is emitted as literal text and only the core is pseudonymized, so remainder
  mappings are shaped like detector spans, which never carry edge whitespace.
  A remainder that is only whitespace mints nothing.

  **Overlaps are fully masked and reversible.** Every character covered by a
  detected span is replaced, whatever the overlap shape — apart from the
  whitespace a remainder is trimmed of, which is emitted as itself — and every
  pseudonym maps back to exactly the text it replaced, a remainder's mapping
  holding the trimmed slice rather than the span it came from. Reveal returns
  identical spellings character for character, bounded by one designed mapper
  property that predates this change and applies to every entity:
  `get_or_create` keys on `strip().lower()` and ignores the entity type, so
  values differing only in case share a pseudonym. The spelling that comes
  back, and the type label in that pseudonym, are those of whichever
  occurrence was minted first — and minting runs back to front, so they come
  from the LAST occurrence in the document ("GmbH … GMBH" reveals as "GMBH"
  twice). With PERSON `[10, 20)` overlapping LOCATION `[15, 25)`, the output
  carries the PERSON pseudonym for `[10, 20)` and a LOCATION pseudonym whose
  original value is `text[20:25]`. This supersedes the interim behaviour on
  this release branch, which skipped the losing entity whole and left those
  five characters in cleartext.

- **DOCX and XLSX reconstruction dropped and mis-assigned replacements.**
  Rewriting an Office file means replacing originals inside paragraph runs and
  cell values, which needs the pairing of original text and placeholder.
  `_build_replacements` re-derived it by walking the entities and accumulating
  an offset shift for each one whose shifted start landed on a `<<`. Any
  placeholder that arithmetic could not predict — the extra one an overlap's
  remainder produces — threw off every later entity: the replacement was
  dropped (the original stayed in the file) or paired with another entity's
  placeholder (reveal would restore the wrong value). Reconstruction now
  consumes the spans `pseudonymize_detailed` emits, applied longest original
  first so a short original cannot eat a longer one containing it. A block
  that carries entities but no record of the substitution is refused rather
  than rewritten from a guess, and the caller falls back to converted text.

  Reconstruction also refuses when the replacement map cannot reproduce the
  masked text. Replacement rewrites the file by text, so an original drawn
  from the placeholder charset can land inside an already-inserted
  placeholder — an ID `12` alongside twelve people would turn
  `<<PERSON_12>>` into `<<PERSON_<<ID_1>>>>`, still masked but no longer
  reversible — and an original can equally hit an occurrence the detector
  never flagged ("Weber" inside "Weberstrasse"). The map is checked against
  the pseudonymized text before it is used; on a mismatch the file is
  converted to text instead, which keeps both the masking and the reveal and
  loses only the formatting.

- **A reconstructed DOCX or XLSX could ship PII the masked text had removed.**
  The writers reach paragraph runs and string cells; documents have surfaces
  that are neither, and the extracted text is not a map of where its
  characters live in the file. Three shapes shipped the original next to its
  own placeholder, and no check saw them: text in a **hyperlink run**
  (python-docx counts it in `Paragraph.text` but leaves it out of
  `Paragraph.runs` — a file came out reading `Kontakt: <<PERSON_1>>Anna
  Beispiel`), an entity **spanning a paragraph break** (the extractor joins
  paragraphs with a newline, so a detected span can match the flat text and no
  paragraph at all), and a **numeric cell** in XLSX (the extractor stringifies
  every cell, the generic writer only rewrites string ones, so a flagged
  customer number stayed put — reachable by calling that writer directly, not
  through the SDK or the pipeline, which route spreadsheets through the
  column-aware path). Reconstruction now verifies the artifact instead of the
  plan: the bytes it produced are extracted again with the same extractor and
  must yield the masked text, otherwise the file is refused and converted to
  text. The guarantee is now stated in one line — a file rewritten by these
  writers is shipped only if extracting it reproduces the masked text — and
  the cost of a refusal is formatting, never masking. Two accepted residuals,
  both on that side of the trade: a document whose replacement would
  over-reach, and one whose PII lives where the writers cannot go, come back
  as text rather than as files. **Follow-up:** the column-aware XLSX path
  (`pseudonymize_xlsx_smart`) produces its own bytes and is returned
  unverified; extending the same check to it is not in this release.

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
