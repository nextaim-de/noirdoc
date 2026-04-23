from __future__ import annotations

from redis.asyncio import Redis


class RedisMappingBackend:
    """Redis implementation of the MappingBackend protocol."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)

    async def get(self, key: str) -> bytes | None:
        return await self._redis.get(key)

    async def delete(self, key: str) -> bool:
        deleted = await self._redis.delete(key)
        return deleted > 0

    async def get_ttl(self, key: str) -> int | None:
        ttl = await self._redis.ttl(key)
        return ttl if ttl >= 0 else None

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False
