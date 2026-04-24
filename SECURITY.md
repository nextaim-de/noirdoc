# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:

**<https://github.com/nextaim-de/noirdoc/security/advisories/new>**

Please do not open a public issue for security bugs. We will acknowledge reports as soon as we can and coordinate a fix and disclosure with you.

Useful things to include:
- A description of the issue and the impact you see.
- A minimal reproducer (input, command, expected vs. actual output).
- Affected version(s) — `noirdoc --version` or the installed package version.
- Python version and OS.

## Scope

Noirdoc is pre-1.0 and changes quickly. Security issues are taken seriously, but formal response-time SLAs and long-term support branches start at 1.0.

What we consider in scope:
- PII that escapes redaction in a supported format.
- Bypass of reversible mapping (leaking real values into output that should be pseudonymized).
- Mis-handling of the per-namespace Fernet key (`~/.noirdoc/namespaces/<name>/`).
- Issues in the CLI, SDK, or mapper backends (`MemoryMappingBackend`, `FileMappingBackend`, `RedisMappingBackend`) that lead to data loss or leakage.

Out of scope:
- Detections missed in unsupported formats (PPTX, images — these are pass-through today).
- False negatives that reflect upstream model limits (Presidio, spaCy, Flair, GLiNER). These are quality issues — open a regular issue with a test case.

## Supported versions

Until `1.0`, only the latest `main` and the latest tagged release receive security fixes.
