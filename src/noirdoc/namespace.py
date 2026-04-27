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
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from noirdoc.pseudonymization.mapper import PseudonymMapper

DEFAULT_NAMESPACE_ROOT = Path.home() / ".noirdoc" / "namespaces"

_KEY_FILE = "key"
_DATA_FILE = "mapper.enc"
# Restrictive whitelist for namespace names. Rejects path traversal
# (``..``), absolute paths, separators, and shell metacharacters so a
# user-supplied namespace cannot escape the configured root.
_NAMESPACE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _validate_namespace_name(name: str) -> str:
    if not _NAMESPACE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid namespace name {name!r}: must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}",
        )
    return name


class Namespace:
    """A persistent, Fernet-encrypted pseudonym mapping on disk."""

    def __init__(self, name: str, root: Path | str | None = None) -> None:
        self.name = _validate_namespace_name(name)
        self.root = Path(root).expanduser() if root else DEFAULT_NAMESPACE_ROOT
        self.path = self.root / self.name

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

        # Create the namespace directory with 0700 *up front* so the key
        # file is never momentarily readable by another user. mkdir's
        # ``mode`` is honored only on creation; umask may have stripped
        # bits, so chmod explicitly afterward.
        self.path.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass

        key = Fernet.generate_key()
        # Atomically create the key file with mode 0600 and refuse to
        # clobber. O_EXCL closes the TOCTOU window where the file might
        # have appeared after the is_file() check above.
        try:
            fd = os.open(
                str(self.key_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self.key_path.read_bytes()
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
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
