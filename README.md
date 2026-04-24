<p align="center">
  <img src="https://raw.githubusercontent.com/nextaim-de/noirdoc/main/docs/assets/banner.svg" alt="noirdoc — German-first PII redaction, local by default." width="800">
</p>

<p align="center">
  <a href="https://github.com/nextaim-de/noirdoc/actions/workflows/ci.yml"><img src="https://github.com/nextaim-de/noirdoc/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13-blue" alt="Python 3.12 | 3.13">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
  <a href="https://github.com/pre-commit/pre-commit"><img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen" alt="pre-commit enabled"></a>
</p>

# noirdoc

**German-first PII redaction and pseudonymization for documents. Local by default. Reversible when you need it.**

Noirdoc redacts names, addresses, phone numbers, IBANs, Steuer-IDs, SVNRs, and the rest — from PDFs, DOCX, XLSX, and plain text — without sending anything to a third party. Under the hood it's a rules-based Presidio pipeline by default, and an ensemble (Presidio + GLiNER + Flair) when the `[full]` extra is installed. It's built for real-world German documents and mixed DE/EN text — the kind of stuff Mittelstand actually runs through an LLM.

> **Status:** alpha (`0.1.x`). API will change before `1.0`. Pin the minor version.

## Prerequisites

- Python **3.12** or **3.13**
- ~1 GB free disk if you install the `[full]` extra (spaCy + Flair + GLiNER weights)
- Optional: a Redis instance if you want shared mapping storage across workers (`[redis]` extra)

## Install

```bash
# Baseline — Presidio + all file extractors + reversible mapper.
pip install noirdoc

# Full ensemble (adds GLiNER + Flair, large ML weights). Recommended for real work.
pip install noirdoc[full]
noirdoc models pull

# Optional distributed mapper backend.
pip install noirdoc[redis]
```

For anything beyond toy examples, use `noirdoc[full]` — the ensemble catches what the baseline misses, especially on German lowercase text.

## Quickstart

```bash
# One-shot redact (ephemeral mapping, discarded on exit).
noirdoc redact vertrag.pdf -o vertrag-clean.pdf

# Persistent namespace — placeholders stay consistent across files and sessions.
noirdoc redact --namespace mandant-mueller brief.docx -o brief-clean.docx
noirdoc reveal --namespace mandant-mueller brief-clean.docx -o brief-revealed.docx
noirdoc lookup --namespace mandant-mueller "<<PERSON_3>>"
```

```python
from noirdoc import Redactor

r = Redactor(namespace="mandant-mueller")
r.redact_file("vertrag.pdf", output="vertrag-clean.pdf")
r.redact_file("brief.docx", output="brief-clean.docx")
r.reveal_text(llm_response)  # un-redact the model's reply
```

Input:

> Anna Müller, geboren am 12.03.1981 in München, erreichbar unter 0171-2345678, Steuer-ID 12 345 678 901, IBAN DE89 3704 0044 0532 0130 00.

Output:

> `<<PERSON_1>>`, geboren am `<<DATE_TIME_1>>` in `<<LOCATION_1>>`, erreichbar unter `<<PHONE_NUMBER_1>>`, Steuer-ID `<<DE_STEUER_ID_1>>`, IBAN `<<IBAN_CODE_1>>`.

## Commands

| Command                      | What it does                                                                           |
|------------------------------|----------------------------------------------------------------------------------------|
| `noirdoc redact <files>`     | Redact one or more files (accepts directories; `-o FILE` or `--output-dir DIR`).       |
| `noirdoc reveal <file>`      | Reverse pseudonyms back to originals (DOCX / XLSX / plain; `--namespace` required).    |
| `noirdoc lookup <token>`     | Resolve a pseudonym like `<<PERSON_1>>` to its original value.                         |
| `noirdoc ns list`            | List persistent namespaces under `~/.noirdoc/namespaces/`.                             |
| `noirdoc ns show <name>`     | Print the mapping summary for a namespace as JSON.                                     |
| `noirdoc ns delete <name>`   | Delete a namespace (prompts for confirmation).                                         |
| `noirdoc models pull`        | Download spaCy models and (optionally) GLiNER weights up front.                        |

Run `noirdoc <cmd> --help` for the full flag list on any subcommand.

## Before you start

A few honest caveats before you ship this into a pipeline:

- **Best results need `[full]`.** On first use (or via `noirdoc models pull`) the full extra downloads roughly **560 MB** of weights: spaCy `de_core_news_lg`, Flair `ner-german-large`, and a GLiNER multilingual model. Budget disk and bandwidth.
- **PDF reveal is not supported yet.** Round-tripping placeholders back into a PDF is a hard problem (position drift, font metrics, image-based redactions). PDFs redact cleanly; reveal is pass-through. DOCX, XLSX, and plain text round-trip fully.
- **Alpha API.** Classes and CLI flags may change between `0.1.x` and `0.2.x`. Pin accordingly.
- **Detector quality depends on the upstream models.** Presidio + Flair + GLiNER do the heavy lifting. Noirdoc adds German-specific recognizers on top, but it does not train models.

## German-first

Noirdoc defaults to German (`language="de"`) with fallback to `["de", "en"]` for mixed documents. What that actually means:

- **Custom recognizers** in `src/noirdoc/detection/presidio_detector.py`:
  - `GermanPhoneRecognizer` — German phone formats (0171-..., +49...)
  - `GermanSVNRRecognizer` — Sozialversicherungsnummer with checksum
  - `GermanSteuerIDRecognizer` — 11-digit Steuer-ID with checksum
  - `InvertedNameRecognizer` — registered for both `de` and `en` to catch "Nachname, Vorname" patterns
- **Flair `ner-german-large`** (XLM-R, F1 92.3 % on CoNLL-03 DE) handles lowercase German text — the case where spaCy tends to drop names.
- **GLiNER multilingual** catches entity types the others miss.
- **German-style lowercase** financial terms, German IBANs, German date formats, and German address patterns are covered in the test suite (`tests/test_presidio_detector.py`).

If you're working with German legal, medical, HR, or financial documents, this is what the defaults are tuned for.

## Supported formats

| Format                       | Redact | Reveal (round-trip) |
|------------------------------|--------|---------------------|
| PDF                          | ✓      | ✗ (pass-through)    |
| DOCX                         | ✓      | ✓                   |
| XLSX                         | ✓      | ✓                   |
| Plain text / CSV / MD / HTML | ✓      | ✓                   |
| PPTX / images                | ✓      | ✗ (pass-through)    |

PDF reveal is an open contribution target — see [CONTRIBUTING.md](https://github.com/nextaim-de/noirdoc/blob/main/CONTRIBUTING.md).

## Advanced: shared mapping storage

The `[redis]` extra ships a `RedisMappingBackend` that plugs into the lower-level `MappingStore` — the same primitive Noirdoc Cloud uses for request-scoped, encrypted, TTL-bounded mapping persistence across workers. It is **not** wired into `Redactor(namespace=...)`, which persists to the local filesystem under `~/.noirdoc/namespaces/`. Use `MappingStore` when you have multiple workers that need to share pseudonym mappings for the same request, or when you want encrypted-at-rest mappings with automatic expiry.

```python
import asyncio
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from noirdoc.mappings.backends.redis_backend import RedisMappingBackend
from noirdoc.mappings.store import MappingStore

async def main() -> None:
    redis = Redis.from_url("redis://localhost:6379")
    store = MappingStore(
        backend=RedisMappingBackend(redis),
        encryption_key=Fernet.generate_key(),  # keep stable across workers
    )
    # store.save(request_id=..., tenant_id=..., mapper=...)
    # mappings = await store.load(request_id)

asyncio.run(main())
```

The `encryption_key` must be identical across workers that need to read the same mappings. `MappingStore.save()` accepts a `ttl_days` kwarg (default 30).

## Noirdoc Cloud

Don't want to run this yourself? **[Noirdoc Cloud](https://noirdoc.de)** is the hosted API wrapper: a privacy-preserving reverse proxy for LLM calls that uses this exact pipeline, plus multi-tenancy, audit, and provider key management. Compliance story: what's on GitHub is what the cloud runs.

## Contributing

Bug reports, detectors, and format support are all welcome. See [CONTRIBUTING.md](https://github.com/nextaim-de/noirdoc/blob/main/CONTRIBUTING.md) for dev setup, tests, and the recognizer pattern.

## Security

Report vulnerabilities via GitHub's private vulnerability reporting — see [SECURITY.md](https://github.com/nextaim-de/noirdoc/blob/main/SECURITY.md). Please don't open public issues for security bugs.

## Changelog

See [CHANGELOG.md](https://github.com/nextaim-de/noirdoc/blob/main/CHANGELOG.md). Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [SemVer](https://semver.org/).

## License

MIT © 2026 Antonio Maiolo / [Nextaim GmbH](https://nextaim.de). See [LICENSE](https://github.com/nextaim-de/noirdoc/blob/main/LICENSE).

---

<p align="center">
  Built by <a href="https://nextaim.de">Nextaim GmbH</a> · <a href="https://noirdoc.de">noirdoc.de</a>
</p>
