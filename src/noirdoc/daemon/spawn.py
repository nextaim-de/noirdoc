"""Detached daemon spawn + stale-socket recovery.

The daemon is launched as ``python -m noirdoc.daemon`` in a new session so
it survives the parent CLI process exiting. Old sockets and stale pidfiles
left behind by a crashed daemon are cleaned up before binding.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from noirdoc.daemon import paths


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pidfile() -> int | None:
    pf = paths.pidfile_path()
    try:
        text = pf.read_text().strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def write_pidfile(pid: int) -> None:
    paths.ensure_root_dir()
    paths.pidfile_path().write_text(f"{pid}\n")


def remove_pidfile() -> None:
    try:
        paths.pidfile_path().unlink()
    except FileNotFoundError:
        pass


def cleanup_stale_socket() -> None:
    """Remove ``daemon.sock`` if no live process owns it.

    A live daemon's PID lives in the pidfile; we trust that record. If the
    pidfile points at a dead PID (or is missing entirely) and a socket file
    is still on disk, it's a leftover from a crash and is safe to remove.
    """
    sock = paths.socket_path()
    if not sock.exists():
        return
    pid = read_pidfile()
    if pid is not None and is_pid_alive(pid):
        return  # Live daemon owns the socket; do not touch.
    try:
        sock.unlink()
    except FileNotFoundError:
        pass
    remove_pidfile()


def spawn_detached() -> int:
    """Fork a fully detached daemon. Returns the spawned PID."""
    paths.ensure_root_dir()
    proc = subprocess.Popen(
        [sys.executable, "-m", "noirdoc.daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def wait_for_socket(timeout_s: float = 30.0) -> bool:
    """Poll for the daemon socket to appear. Backoff 100→500 ms."""
    sock = paths.socket_path()
    deadline = time.monotonic() + timeout_s
    delay = 0.1
    while time.monotonic() < deadline:
        if sock.exists():
            return True
        time.sleep(delay)
        delay = min(delay * 1.5, 0.5)
    return False


def stop_daemon(timeout_s: float = 5.0) -> bool:
    """Send SIGTERM to the running daemon (if any) and wait for exit.

    Returns ``True`` if a daemon was running and stopped (or was already
    gone), ``False`` if the PID was alive but didn't exit in time.
    """
    pid = read_pidfile()
    if pid is None or not is_pid_alive(pid):
        cleanup_stale_socket()
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        cleanup_stale_socket()
        return True

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            cleanup_stale_socket()
            return True
        time.sleep(0.1)
    return False


def daemon_log_handle() -> tuple[Path, int]:
    """Open ``daemon.log`` for append, returning (path, fd)."""
    paths.ensure_root_dir()
    log = paths.logfile_path()
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return log, fd
