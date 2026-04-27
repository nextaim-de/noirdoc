"""Spawn / pidfile / stale-socket tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noirdoc.daemon import paths, spawn


@pytest.fixture
def isolated_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("NOIRDOC_DAEMON_ROOT", str(tmp_path))
    monkeypatch.delenv("NOIRDOC_DAEMON_SOCKET", raising=False)
    return tmp_path


def test_pidfile_roundtrip(isolated_root: Path):
    assert spawn.read_pidfile() is None
    spawn.write_pidfile(12345)
    assert spawn.read_pidfile() == 12345
    spawn.remove_pidfile()
    assert spawn.read_pidfile() is None


def test_pidfile_invalid_content(isolated_root: Path):
    paths.pidfile_path().write_text("not-an-int\n")
    assert spawn.read_pidfile() is None


def test_is_pid_alive_self():
    assert spawn.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_dead():
    # PID 0 is conventionally invalid; we treat <=0 as dead.
    assert spawn.is_pid_alive(0) is False


def test_cleanup_stale_socket_removes_when_no_owner(isolated_root: Path):
    sock = paths.socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_bytes(b"")  # leftover from a crash
    # No pidfile → no live owner → socket should go.
    spawn.cleanup_stale_socket()
    assert not sock.exists()


def test_cleanup_stale_socket_removes_when_pid_dead(isolated_root: Path):
    sock = paths.socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_bytes(b"")
    spawn.write_pidfile(0)  # 0 is not alive in our check
    spawn.cleanup_stale_socket()
    assert not sock.exists()
    assert spawn.read_pidfile() is None


def test_cleanup_stale_socket_preserves_when_owner_alive(isolated_root: Path):
    sock = paths.socket_path()
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_bytes(b"")
    spawn.write_pidfile(os.getpid())  # this very process is alive
    spawn.cleanup_stale_socket()
    assert sock.exists()  # left alone


def test_stop_daemon_when_not_running(isolated_root: Path):
    assert spawn.stop_daemon(timeout_s=0.5) is True
