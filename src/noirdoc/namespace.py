"""Persistent namespace storage for reversible pseudonym mappings.

A namespace is a directory under ``~/.noirdoc/namespaces/<name>/`` holding
a Fernet key and an encrypted serialized :class:`PseudonymMapper`. Used by
``noirdoc redact --namespace foo`` so that the same entity receives the
same pseudonym across invocations, and by ``noirdoc reveal --namespace foo``
to restore originals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from noirdoc.pseudonymization.mapper import PseudonymMapper

DEFAULT_NAMESPACE_ROOT = Path.home() / ".noirdoc" / "namespaces"

_KEY_FILE = "key"
_DATA_FILE = "mapper.enc"


class Namespace:
    """A persistent, Fernet-encrypted pseudonym mapping on disk."""

    def __init__(self, name: str, root: Path | str | None = None) -> None:
        self.name = name
        self.root = Path(root).expanduser() if root else DEFAULT_NAMESPACE_ROOT
        self.path = self.root / name

    @property
    def key_path(self) -> Path:
        return self.path / _KEY_FILE

    @property
    def data_path(self) -> Path:
        return self.path / _DATA_FILE

    def exists(self) -> bool:
        return self.key_path.is_file()

    def _ensure_key(self) -> bytes:
        if self.key_path.is_file():
            return self.key_path.read_bytes()

        self.path.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        os.chmod(self.key_path, 0o600)
        return key

    def load(self) -> PseudonymMapper:
        """Return the mapper for this namespace, creating an empty one if needed."""
        self._ensure_key()
        if not self.data_path.is_file():
            return PseudonymMapper()

        key = self.key_path.read_bytes()
        blob = self.data_path.read_bytes()
        try:
            plaintext = Fernet(key).decrypt(blob)
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError(
                f"Failed to decrypt namespace {self.name!r} — key/data mismatch",
            ) from exc
        data = json.loads(plaintext.decode("utf-8"))
        return PseudonymMapper.from_dict(data)

    def save(self, mapper: PseudonymMapper) -> None:
        """Encrypt and persist the mapper state."""
        key = self._ensure_key()
        plaintext = json.dumps(mapper.to_dict(), ensure_ascii=False).encode("utf-8")
        blob = Fernet(key).encrypt(plaintext)
        tmp = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(self.data_path)
        os.chmod(self.data_path, 0o600)

    def delete(self) -> None:
        """Remove the namespace directory and all its contents."""
        for f in (self.data_path, self.key_path):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        try:
            self.path.rmdir()
        except OSError:
            pass


def list_namespaces(root: Path | str | None = None) -> list[str]:
    base = Path(root).expanduser() if root else DEFAULT_NAMESPACE_ROOT
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / _KEY_FILE).is_file())
