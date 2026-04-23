# noirdoc

Local PII redaction and pseudonymization for documents. MIT-licensed.

> **Status:** early scaffold. API unstable; not yet published to PyPI.

Noirdoc redacts PII from PDFs, DOCX, XLSX, and plain text — locally, without
sending data to any third-party service. It uses a rules-based detector
(Presidio) by default and an ensemble (Presidio + GLiNER + Flair) when the
`[full]` extra is installed.

## Install

```bash
# Baseline — Presidio + all file extractors + reversible mapper.
pip install noirdoc

# Full ensemble (adds GLiNER + Flair, large ML weights).
pip install noirdoc[full]
noirdoc models pull

# Optional distributed mapper backend.
pip install noirdoc[redis]
```

## Quickstart

```bash
# One-shot redact (ephemeral mapping, discarded on exit).
noirdoc redact input.pdf -o output.pdf

# Persistent namespace — placeholders stay consistent across files and sessions.
noirdoc redact --namespace client-acme brief.docx -o brief-clean.docx
noirdoc reveal --namespace client-acme brief-clean.docx -o brief-revealed.docx
noirdoc lookup --namespace client-acme "<<PERSON_3>>"
```

```python
from noirdoc import Redactor

r = Redactor(namespace="client-acme")
r.redact_file("input.pdf", output="output.pdf")
r.redact_file("brief.docx", output="brief-clean.docx")
r.reveal_text(llm_response)
```

## Format support

| Format                     | Redact | Reveal (round-trip) |
|----------------------------|--------|---------------------|
| PDF                        | ✓      | ✗ (pass-through)    |
| DOCX                       | ✓      | ✓                   |
| XLSX                       | ✓      | ✓                   |
| Plain text / CSV / MD / HTML | ✓    | ✓                   |
| PPTX / images              | ✓      | ✗ (pass-through)    |

PDF reveal is a hard problem (position drift, font metrics, image-based
redactions). It is not in v0.1; it is an open contribution target.

## Why Noirdoc

- **Local-first.** No data leaves your machine. No API keys required for the
  baseline install.
- **Reversible.** Pseudonyms like `<<PERSON_1>>` are stable within a namespace
  so LLM responses can be un-redacted back to the originals.
- **Multi-format.** The same pipeline handles PDF, DOCX, XLSX, and plain
  text — no glue code on your side.

## Noirdoc Cloud

This package is the redaction core that powers [Noirdoc Cloud](#) — a hosted
LLM proxy that adds tenancy, audit, and provider forwarding. The pipeline
code is identical: cloud literally imports this package.

That is the compliance story: what is on GitHub is what cloud runs.

## License

MIT. See [LICENSE](LICENSE).
