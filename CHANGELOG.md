# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Baseline install (`pip install noirdoc`) crashed with `ModuleNotFoundError:
  gliner` because the default `detector="ensemble"` eagerly imported GLiNER.
  The ensemble now detects an absent GLiNER, falls back to Presidio-only with
  a `UserWarning`, and keeps working. Explicit `--detector gliner` still fails
  loudly when the `[full]` extra isn't installed.

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
