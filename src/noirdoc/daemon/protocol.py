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

# Per-field caps. Bound so a malicious or buggy client cannot wedge the
# daemon with a multi-gigabyte JSON payload. Tunable here; matched by
# the asyncio buffer limit in server.py / client.py.
MAX_TEXT_VALUE_LEN = 16 * 1024 * 1024  # 16 MB, covers very large texts
MAX_PATH_LEN = 4096  # POSIX PATH_MAX
MAX_NAMESPACE_LEN = 64
MAX_DETECTOR_MODEL_LEN = 512


class HelloParams(BaseModel):
    client_version: str = Field(max_length=64)


class HelloResult(BaseModel):
    daemon_version: str
    pid: int
    started_at: float


class RedactTextInput(BaseModel):
    type: Literal["text"] = "text"
    value: str = Field(max_length=MAX_TEXT_VALUE_LEN)


class RedactFileInput(BaseModel):
    type: Literal["file"] = "file"
    path: str = Field(max_length=MAX_PATH_LEN)


RedactInput = Annotated[
    Union[RedactTextInput, RedactFileInput],
    Field(discriminator="type"),
]


class RedactParams(BaseModel):
    namespace: str | None = Field(default=None, max_length=MAX_NAMESPACE_LEN)
    namespace_root: str | None = Field(default=None, max_length=MAX_PATH_LEN)
    language: str = Field(default="de", max_length=8)
    detector: DetectorChoice = "ensemble"
    score_threshold: float = 0.5
    gliner_model: str = Field(
        default="knowledgator/gliner-pii-edge-v1.0",
        max_length=MAX_DETECTOR_MODEL_LEN,
    )
    input: RedactInput
    output_path: str | None = Field(default=None, max_length=MAX_PATH_LEN)


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
