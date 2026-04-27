"""Pydantic round-trip tests for the daemon wire protocol."""

from __future__ import annotations

import json

import pytest

from noirdoc.daemon.protocol import (
    ErrorPayload,
    HelloParams,
    HelloResult,
    RedactFileInput,
    RedactParams,
    RedactResult,
    RedactTextInput,
    Request,
    Response,
    ShutdownResult,
    StatusResult,
)


def _roundtrip(model):
    data = model.model_dump()
    serialized = json.dumps(data)
    return type(model).model_validate(json.loads(serialized))


def test_hello_params_roundtrip():
    assert _roundtrip(HelloParams(client_version="1.2.3")).client_version == "1.2.3"


def test_hello_result_roundtrip():
    out = _roundtrip(HelloResult(daemon_version="1.2.3", pid=42, started_at=10.0))
    assert out.daemon_version == "1.2.3"
    assert out.pid == 42


def test_redact_text_input_roundtrip():
    p = RedactParams(input=RedactTextInput(value="hello world"))
    out = _roundtrip(p)
    assert isinstance(out.input, RedactTextInput)
    assert out.input.value == "hello world"


def test_redact_file_input_roundtrip():
    p = RedactParams(input=RedactFileInput(path="/tmp/foo.txt"), output_path="/tmp/out.txt")
    out = _roundtrip(p)
    assert isinstance(out.input, RedactFileInput)
    assert out.input.path == "/tmp/foo.txt"
    assert out.output_path == "/tmp/out.txt"


def test_redact_params_defaults():
    p = RedactParams(input=RedactTextInput(value="x"))
    assert p.language == "de"
    assert p.detector == "ensemble"
    assert p.score_threshold == 0.5


def test_redact_input_discriminator_rejects_unknown_type():
    with pytest.raises(Exception):
        RedactParams.model_validate({"input": {"type": "garbage", "value": "x"}})


def test_redact_result_optional_fields():
    r = RedactResult(entity_count=3, entity_types={"PERSON": 2, "EMAIL": 1})
    assert r.redacted_text is None
    assert r.output_path is None
    assert r.reconstructed is False


def test_status_result_roundtrip():
    s = StatusResult(
        uptime_s=10.5,
        models_loaded=True,
        last_request_at=None,
        queue_depth=0,
        total_requests=0,
    )
    out = _roundtrip(s)
    assert out.uptime_s == 10.5
    assert out.last_request_at is None


def test_shutdown_result_default():
    assert ShutdownResult().ok is True


def test_request_envelope_roundtrip():
    req = Request(id="abc", method="redact", params={"foo": "bar"})
    out = _roundtrip(req)
    assert out.id == "abc"
    assert out.method == "redact"
    assert out.params == {"foo": "bar"}


def test_response_envelope_with_result():
    resp = Response(id="abc", result={"ok": True})
    out = _roundtrip(resp)
    assert out.result == {"ok": True}
    assert out.error is None


def test_response_envelope_with_error():
    resp = Response(id="abc", error=ErrorPayload(code="bad_request", message="x"))
    out = _roundtrip(resp)
    assert out.error is not None
    assert out.error.code == "bad_request"
