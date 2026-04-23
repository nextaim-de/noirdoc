from __future__ import annotations

from noirdoc.mappings.backends.base import MappingBackend
from noirdoc.mappings.backends.file import FileMappingBackend
from noirdoc.mappings.backends.memory import MemoryMappingBackend

__all__ = [
    "FileMappingBackend",
    "MappingBackend",
    "MemoryMappingBackend",
    "RedisMappingBackend",
]


def __getattr__(name: str):
    if name == "RedisMappingBackend":
        from noirdoc.mappings.backends.redis_backend import RedisMappingBackend

        return RedisMappingBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
