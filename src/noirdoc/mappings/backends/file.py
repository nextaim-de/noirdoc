from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path

_HEADER = struct.Struct(">Q")  # 8-byte BE unix timestamp; 0 = no expiry


def _safe_key(key: str) -> str:
    """Map an arbitrary key to a filesystem-safe filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in key)


class FileMappingBackend:
    """Filesystem-backed implementation of the MappingBackend protocol.

    Each key is stored as a single file under ``root``. The first 8 bytes
    encode the expiry as a big-endian unix timestamp (0 = no expiry); the
    remainder is the opaque value. TTLs are enforced lazily on read.

    Used for persistent CLI namespaces (``noirdoc redact --namespace ...``),
    where a single mapping blob per namespace is the common case.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._root / _safe_key(key)

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        expires_at = int(time.time()) + ttl_seconds if ttl_seconds > 0 else 0
        payload = _HEADER.pack(expires_at) + value
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        await asyncio.to_thread(tmp.write_bytes, payload)
        await asyncio.to_thread(tmp.replace, path)
        await asyncio.to_thread(path.chmod, 0o600)

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            payload = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None
        if len(payload) < _HEADER.size:
            return None
        (expires_at,) = _HEADER.unpack(payload[: _HEADER.size])
        if expires_at and expires_at <= int(time.time()):
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return None
        return payload[_HEADER.size :]

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return False
        return True

    async def get_ttl(self, key: str) -> int | None:
        path = self._path(key)
        try:
            header = await asyncio.to_thread(_read_header, path)
        except FileNotFoundError:
            return None
        if header is None:
            return None
        if header == 0:
            return None
        remaining = header - int(time.time())
        return remaining if remaining > 0 else None

    async def ping(self) -> bool:
        return self._root.is_dir()


def _read_header(path: Path) -> int | None:
    with path.open("rb") as f:
        buf = f.read(_HEADER.size)
    if len(buf) < _HEADER.size:
        return None
    (expires_at,) = _HEADER.unpack(buf)
    return expires_at
