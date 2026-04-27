"""Verify the daemon serializes redact requests via its single asyncio lock."""

from __future__ import annotations

import asyncio
import time

import pytest

from noirdoc.daemon import server

pytestmark = pytest.mark.asyncio


async def test_handle_redact_serializes(monkeypatch):
    """Two concurrent ``handle_redact`` calls must run back-to-back, not in parallel."""
    state = server.DaemonState()

    sleep_for = 0.15

    class FakeMapper:
        entity_count = 0

    class FakeRedactor:
        mapper = FakeMapper()

        async def aredact_text_detailed(self, text, language):
            await asyncio.sleep(sleep_for)
            return text, []

    async def fake_get_detectors(*args, **kwargs):
        return []

    monkeypatch.setattr(state, "get_detectors", fake_get_detectors)
    monkeypatch.setattr(
        "noirdoc.sdk.build_redactor",
        lambda **kwargs: FakeRedactor(),
    )

    params = {
        "namespace": None,
        "language": "de",
        "detector": "ensemble",
        "score_threshold": 0.5,
        "input": {"type": "text", "value": "hello"},
    }

    t0 = time.monotonic()
    results = await asyncio.gather(
        server.handle_redact(state, params),
        server.handle_redact(state, params),
    )
    elapsed = time.monotonic() - t0

    assert elapsed >= sleep_for * 1.8, (
        f"expected serialized (~{sleep_for * 2}s), got {elapsed:.3f}s"
    )
    assert len(results) == 2
    assert state.total_requests == 2
    assert state.queue_depth == 0


async def test_queue_depth_tracks_pending(monkeypatch):
    """While one request holds the lock, a queued request bumps queue_depth above 1."""
    state = server.DaemonState()

    proceed = asyncio.Event()

    class FakeMapper:
        entity_count = 0

    class FakeRedactor:
        mapper = FakeMapper()

        async def aredact_text_detailed(self, text, language):
            await proceed.wait()
            return text, []

    async def fake_get_detectors(*args, **kwargs):
        return []

    monkeypatch.setattr(state, "get_detectors", fake_get_detectors)
    monkeypatch.setattr(
        "noirdoc.sdk.build_redactor",
        lambda **kwargs: FakeRedactor(),
    )

    params = {
        "namespace": None,
        "language": "de",
        "detector": "ensemble",
        "score_threshold": 0.5,
        "input": {"type": "text", "value": "hello"},
    }

    task1 = asyncio.create_task(server.handle_redact(state, params))
    task2 = asyncio.create_task(server.handle_redact(state, params))

    # Yield repeatedly so both tasks reach the lock-acquire point.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if state.queue_depth >= 2:
            break

    assert state.queue_depth == 2

    proceed.set()
    await asyncio.gather(task1, task2)
    assert state.queue_depth == 0
