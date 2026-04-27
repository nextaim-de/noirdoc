"""Filesystem paths used by the daemon (socket, pidfile, log)."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".noirdoc"


def root_dir() -> Path:
    """Directory holding daemon state. Honors ``NOIRDOC_DAEMON_ROOT`` for tests."""
    override = os.environ.get("NOIRDOC_DAEMON_ROOT")
    if override:
        return Path(override).expanduser()
    return DEFAULT_ROOT


def socket_path() -> Path:
    override = os.environ.get("NOIRDOC_DAEMON_SOCKET")
    if override:
        return Path(override).expanduser()
    return root_dir() / "daemon.sock"


def pidfile_path() -> Path:
    return root_dir() / "daemon.pid"


def logfile_path() -> Path:
    return root_dir() / "daemon.log"


def ensure_root_dir() -> Path:
    """Create the daemon root with 0o700 perms, idempotent."""
    d = root_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d
