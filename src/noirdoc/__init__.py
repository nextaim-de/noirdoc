"""noirdoc — local PII redaction and pseudonymization for documents."""

from __future__ import annotations

from noirdoc.sdk import RedactionResult, Redactor, redact

try:
    from noirdoc._version import __version__  # type: ignore[import-not-found]
except ImportError:  # Source checkout without a build step (e.g. plain `pytest`).
    __version__ = "0.0.0+unknown"

__all__ = ["RedactionResult", "Redactor", "__version__", "redact"]
