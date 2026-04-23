"""Tests for the FileMappingBackend and MemoryMappingBackend implementations."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from noirdoc.mappings.backends import (
    FileMappingBackend,
    MappingBackend,
    MemoryMappingBackend,
)


@pytest.fixture(params=["memory", "file"])
async def backend(request, tmp_path: Path) -> MappingBackend:
    if request.param == "memory":
        return MemoryMappingBackend()
    return FileMappingBackend(tmp_path / "ns")


async def test_set_get_roundtrip(backend: MappingBackend):
    await backend.set("k", b"hello", ttl_seconds=0)
    assert await backend.get("k") == b"hello"


async def test_get_missing_key(backend: MappingBackend):
    assert await backend.get("nope") is None


async def test_delete(backend: MappingBackend):
    await backend.set("k", b"v", ttl_seconds=0)
    assert await backend.delete("k") is True
    assert await backend.delete("k") is False
    assert await backend.get("k") is None


async def test_ttl_reported(backend: MappingBackend):
    await backend.set("k", b"v", ttl_seconds=60)
    remaining = await backend.get_ttl("k")
    assert remaining is not None and 0 < remaining <= 60


async def test_no_ttl_reported_for_permanent(backend: MappingBackend):
    await backend.set("k", b"v", ttl_seconds=0)
    assert await backend.get_ttl("k") is None


async def test_ttl_expiry(backend: MappingBackend):
    await backend.set("k", b"v", ttl_seconds=1)
    time.sleep(1.1)
    assert await backend.get("k") is None


async def test_ping(backend: MappingBackend):
    assert await backend.ping() is True


async def test_file_backend_roundtrip_across_instances(tmp_path: Path):
    root = tmp_path / "store"
    b1 = FileMappingBackend(root)
    await b1.set("persistent-key", b"persistent-value", ttl_seconds=0)

    b2 = FileMappingBackend(root)
    assert await b2.get("persistent-key") == b"persistent-value"


async def test_file_backend_sanitizes_keys(tmp_path: Path):
    b = FileMappingBackend(tmp_path / "s")
    await b.set("weird/key:with:chars", b"ok", ttl_seconds=0)
    assert await b.get("weird/key:with:chars") == b"ok"


async def test_memory_backend_isolated_instances():
    b1 = MemoryMappingBackend()
    b2 = MemoryMappingBackend()
    await b1.set("k", b"v", ttl_seconds=0)
    assert await b2.get("k") is None
