"""Version-mismatch handling: client should ask the stale daemon to shut down and respawn.

These tests stand up a tiny in-process Unix-socket server that pretends to be
the daemon and inspect the sequence of methods the client invokes. No real
``noirdoc-daemon`` subprocess is involved.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

import pytest

from noirdoc.daemon import client as daemon_client
from noirdoc.daemon import paths


@pytest.fixture
def isolated_paths(monkeypatch):
    # AF_UNIX paths are limited to ~104 bytes on macOS, so pytest tmp_path
    # (which nests deeply) overflows. Use a short /tmp path instead.
    short_dir = Path(tempfile.gettempdir()) / f"nd-{uuid.uuid4().hex[:8]}"
    short_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NOIRDOC_DAEMON_ROOT", str(short_dir))
    monkeypatch.setenv("NOIRDOC_DAEMON_SOCKET", str(short_dir / "d.sock"))
    yield short_dir
    for f in short_dir.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        short_dir.rmdir()
    except OSError:
        pass


class _FakeDaemon:
    """Minimal Unix-socket server scripted with daemon_version per-connection."""

    def __init__(self, socket_path: Path, version: str):
        self.socket_path = socket_path
        self.version = version
        self.received: list[dict] = []
        self.server: asyncio.AbstractServer | None = None
        self._stop_after_shutdown = False

    async def start(self):
        self.server = await asyncio.start_unix_server(
            self._handle,
            path=str(self.socket_path),
        )

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                req = json.loads(line.decode())
                self.received.append(req)
                method = req["method"]
                if method == "hello":
                    result = {"daemon_version": self.version, "pid": 999, "started_at": 0.0}
                elif method == "shutdown":
                    result = {"ok": True}
                    self._stop_after_shutdown = True
                else:
                    result = {"echoed": method}
                resp = {"id": req["id"], "result": result}
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
                if self._stop_after_shutdown:
                    return
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if self._stop_after_shutdown and self.server is not None:
                self.server.close()

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


@pytest.mark.asyncio
async def test_matching_version_passes_through(isolated_paths):
    sock = paths.socket_path()
    daemon = _FakeDaemon(sock, version=daemon_client.__version__)
    await daemon.start()
    try:
        result = await daemon_client.call("status", {})
        assert result.get("echoed") == "status"
        methods = [m["method"] for m in daemon.received]
        assert methods == ["hello", "status"]
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_version_mismatch_triggers_shutdown_and_respawn(
    isolated_paths,
    monkeypatch,
):
    """First connection (stale version) must receive ``shutdown``; the client
    then sees a fresh daemon brought up at the matching version."""
    sock = paths.socket_path()

    stale = _FakeDaemon(sock, version="0.0.0-stale")
    await stale.start()

    # When the client calls _spawn_and_connect after the stale daemon dies,
    # we don't actually want to fork a real daemon. Patch it to bring up a
    # fresh fake at the matching version.
    fresh: dict = {"daemon": None}

    async def fake_spawn_and_connect(socket_path: Path):
        fresh_d = _FakeDaemon(socket_path, version=daemon_client.__version__)
        await fresh_d.start()
        fresh["daemon"] = fresh_d
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        return reader, writer

    monkeypatch.setattr(daemon_client, "_spawn_and_connect", fake_spawn_and_connect)

    try:
        result = await daemon_client.call("status", {})
        assert result.get("echoed") == "status"
        # Stale daemon must have seen hello, then shutdown.
        stale_methods = [m["method"] for m in stale.received]
        assert stale_methods[0] == "hello"
        assert "shutdown" in stale_methods
        # Fresh daemon must have seen hello, then status.
        assert fresh["daemon"] is not None
        fresh_methods = [m["method"] for m in fresh["daemon"].received]
        assert fresh_methods == ["hello", "status"]
    finally:
        await stale.stop()
        if fresh["daemon"] is not None:
            await fresh["daemon"].stop()
