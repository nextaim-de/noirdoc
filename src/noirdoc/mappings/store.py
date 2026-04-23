from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from cryptography.fernet import Fernet

from noirdoc.mappings.backends import MappingBackend
from noirdoc.pseudonymization.mapper import PseudonymMapper

logger = structlog.get_logger()

DEFAULT_MAPPING_TTL_DAYS = 30


class MappingStore:
    """
    Persists PseudonymMapper state, encrypted with Fernet, keyed by request_id.

    Storage is delegated to a MappingBackend (Redis, file, memory, ...) so the
    store itself has no dependency on any specific backing system. This is the
    OSS-bound core: just encrypted snapshot persistence with TTL.

    Cloud-specific concerns (provider-response-id → request-id links, chat-
    completions fingerprint links, code-execution file links) live in
    ``SessionLinkStore`` and are not part of this class.
    """

    KEY_PREFIX = "mapping:"

    def __init__(self, backend: MappingBackend, encryption_key: str | bytes) -> None:
        self._backend = backend
        self._fernet = Fernet(
            encryption_key.encode() if isinstance(encryption_key, str) else encryption_key,
        )

    def _key(self, request_id: uuid.UUID) -> str:
        return f"{self.KEY_PREFIX}{request_id}"

    async def save(
        self,
        *,
        request_id: uuid.UUID,
        tenant_id: uuid.UUID,
        mapper: PseudonymMapper,
        ttl_days: int = DEFAULT_MAPPING_TTL_DAYS,
    ) -> None:
        """
        Save mapper state. Only if mapper has entities.
        Non-blocking: errors are logged, not raised.
        """
        if mapper.entity_count == 0:
            return

        try:
            payload = {
                "request_id": str(request_id),
                "tenant_id": str(tenant_id),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mappings": mapper.get_mapping_summary(),
            }

            plaintext = json.dumps(payload, ensure_ascii=False)
            encrypted = self._fernet.encrypt(plaintext.encode("utf-8"))

            ttl_seconds = ttl_days * 86400
            await self._backend.set(self._key(request_id), encrypted, ttl_seconds)

            logger.info(
                "mapping.saved",
                request_id=str(request_id),
                entity_count=mapper.entity_count,
                ttl_days=ttl_days,
            )
        except Exception as e:
            logger.error(
                "mapping.save_failed",
                request_id=str(request_id),
                error=str(e),
            )

    async def load(self, request_id: uuid.UUID) -> dict[str, str] | None:
        """
        Load mapping. Returns pseudonym->original dict, or None if not found/expired.
        """
        encrypted = await self._backend.get(self._key(request_id))
        if not encrypted:
            return None

        try:
            plaintext = self._fernet.decrypt(encrypted)
            payload = json.loads(plaintext.decode("utf-8"))
            return payload.get("mappings", {})
        except Exception as e:
            logger.error(
                "mapping.load_failed",
                request_id=str(request_id),
                error=str(e),
            )
            return None

    async def load_full(self, request_id: uuid.UUID) -> dict | None:
        """Load full mapping payload including metadata. For admin/debug."""
        encrypted = await self._backend.get(self._key(request_id))
        if not encrypted:
            return None

        try:
            plaintext = self._fernet.decrypt(encrypted)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            logger.error("mapping.load_full_failed", error=str(e))
            return None

    async def delete(self, request_id: uuid.UUID) -> bool:
        """Delete mapping manually (e.g. GDPR/DSGVO request)."""
        deleted = await self._backend.delete(self._key(request_id))
        if deleted:
            logger.info("mapping.deleted", request_id=str(request_id))
        return deleted

    async def get_ttl(self, request_id: uuid.UUID) -> int | None:
        """Remaining TTL in seconds. None if key does not exist."""
        return await self._backend.get_ttl(self._key(request_id))

    async def ping(self) -> bool:
        """Health check: is the backend reachable?"""
        return await self._backend.ping()
