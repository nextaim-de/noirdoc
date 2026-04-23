from __future__ import annotations

import time


class MemoryMappingBackend:
    """In-memory implementation of the MappingBackend protocol.

    Lives for the lifetime of the process. Suitable for one-shot CLI use
    and tests. TTLs are enforced lazily on read.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, float | None]] = {}

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else None
        self._data[key] = (value, expires_at)

    async def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.time():
            del self._data[key]
            return None
        return value

    async def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    async def get_ttl(self, key: str) -> int | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        _, expires_at = entry
        if expires_at is None:
            return None
        remaining = int(expires_at - time.time())
        return remaining if remaining > 0 else None

    async def ping(self) -> bool:
        return True
