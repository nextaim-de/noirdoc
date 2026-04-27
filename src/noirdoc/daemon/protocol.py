"""Wire types for the daemon JSON-lines protocol.

One source of truth for both server (``noirdoc/daemon/server.py``) and
client (``noirdoc/daemon/client.py``) so they cannot drift.

Message shape::

    request:  {"id": "<uuid>", "method": "<name>", "params": {...}}
    response: {"id": "<uuid>", "result": {...}}
              {"id": "<uuid>", "error": {"code": "...", "message": "..."}}

Each message is a single JSON object terminated by ``\\n``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

DetectorChoice = Literal["presidio", "gliner", "ensemble"]


class HelloParams(BaseModel):
    client_version: str


class HelloResult(BaseModel):
    daemon_version: str
    pid: int
    started_at: float


class RedactTextInput(BaseModel):
    type: Literal["text"] = "text"
    value: str


class RedactFileInput(BaseModel):
    type: Literal["file"] = "file"
    path: str  # absolute path on the daemon's filesystem (same user as CLI)


RedactInput = Annotated[
    Union[RedactTextInput, RedactFileInput],
    Field(discriminator="type"),
]


class RedactParams(BaseModel):
    namespace: str | None = None
    namespace_root: str | None = None
    language: str = "de"
    detector: DetectorChoice = "ensemble"
    score_threshold: float = 0.5
    gliner_model: str = "knowledgator/gliner-pii-edge-v1.0"
    input: RedactInput
    output_path: str | None = None  # for file input; daemon writes here directly


class RedactResult(BaseModel):
    redacted_text: str | None = None  # populated for text input
    output_path: str | None = None  # populated for file input written to disk
    entity_count: int
    entity_types: dict[str, int]
    mime_type: str | None = None
    reconstructed: bool = False
    namespace_size: int | None = None


class StatusResult(BaseModel):
    uptime_s: float
    models_loaded: bool
    last_request_at: float | None
    queue_depth: int
    total_requests: int


class ShutdownResult(BaseModel):
    ok: bool = True


class ErrorPayload(BaseModel):
    code: str
    message: str


class Request(BaseModel):
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel):
    id: str
    result: dict[str, Any] | None = None
    error: ErrorPayload | None = None


# Error codes used in Response.error.code
ERR_BAD_REQUEST = "bad_request"
ERR_UNKNOWN_METHOD = "unknown_method"
ERR_INTERNAL = "internal"
ERR_VERSION_MISMATCH = "version_mismatch"
