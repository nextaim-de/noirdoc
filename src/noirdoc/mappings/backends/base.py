from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MappingBackend(Protocol):
    """
    Storage primitive for encrypted mapping snapshots.

    Backends deal in opaque bytes with a TTL. Serialization and encryption are
    the caller's responsibility (see MappingStore). This keeps the backend
    surface small enough to be trivially satisfiable by in-memory, file, or
    Redis implementations.
    """

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...

    async def get(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> bool: ...

    async def get_ttl(self, key: str) -> int | None: ...

    async def ping(self) -> bool: ...
