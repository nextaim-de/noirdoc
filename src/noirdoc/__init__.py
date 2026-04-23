"""noirdoc — local PII redaction and pseudonymization for documents."""

from __future__ import annotations

from noirdoc.sdk import RedactionResult, Redactor, redact

__version__ = "0.1.0"
__all__ = ["RedactionResult", "Redactor", "__version__", "redact"]
