"""CLI-side helpers for talking to the daemon.

Used by ``noirdoc redact`` to transparently spawn and call the daemon.
On any daemon failure (no spawn, crash mid-request, version mismatch
that won't reconcile), the caller is expected to fall back to in-process
redaction so the user's command still succeeds.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from noirdoc import __version__
from noirdoc.daemon import paths, spawn

CONNECT_TIMEOUT = 2.0
RPC_TIMEOUT = 600.0  # generous; covers cold-spawn warmup + slow file redaction
SHUTDOWN_DRAIN_TIMEOUT = 5.0
# Match the server-side cap (server.SOCKET_READ_LIMIT). Bounds the
# memory the client will buffer if the daemon sends an oversize line.
SOCKET_READ_LIMIT = 32 * 1024 * 1024


class DaemonError(Exception):
    """Daemon returned an error response or violated the protocol."""


class DaemonUnavailable(DaemonError):
    """Daemon could not be reached even after attempting to spawn."""


async def _try_connect(
    socket_path: Path,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    try:
        return await asyncio.wait_for(
            asyncio.open_unix_connection(path=str(socket_path), limit=SOCKET_READ_LIMIT),
            timeout=CONNECT_TIMEOUT,
        )
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError):
        return None


async def _spawn_and_connect(
    socket_path: Path,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    spawn.cleanup_stale_socket()
    spawn.spawn_detached()
    ready = await asyncio.to_thread(spawn.wait_for_socket, 30.0)
    if not ready:
        raise DaemonUnavailable("daemon did not bind socket within 30s")
    conn = await _try_connect(socket_path)
    if conn is None:
        raise DaemonUnavailable("could not connect to spawned daemon")
    return conn


async def _send_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float = RPC_TIMEOUT,
) -> dict[str, Any]:
    req_id = uuid.uuid4().hex
    payload = json.dumps(
        {"id": req_id, "method": method, "params": params},
        ensure_ascii=False,
    )
    writer.write(payload.encode("utf-8") + b"\n")
    await writer.drain()

    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    if not line:
        raise DaemonUnavailable("daemon closed connection before sending a response")

    try:
        response = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DaemonError(f"malformed response: {exc}") from exc

    if response.get("id") != req_id:
        raise DaemonError(
            f"response id mismatch: got {response.get('id')!r}, want {req_id!r}",
        )

    if "error" in response and response["error"] is not None:
        err = response["error"]
        raise DaemonError(f"{err.get('code', '?')}: {err.get('message', '?')}")

    result = response.get("result")
    if result is None:
        raise DaemonError("response missing both 'result' and 'error'")
    return result


async def _wait_socket_gone(socket_path: Path, timeout: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not socket_path.exists():
            return
        await asyncio.sleep(0.1)


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Connect (spawning if needed), handshake, send one RPC, return its result.

    On version mismatch, asks the running daemon to shut down, waits for the
    socket to disappear, and tries once more with a freshly spawned daemon.
    """
    socket_path = paths.socket_path()
    params = params or {}

    for attempt in (0, 1):
        conn = await _try_connect(socket_path)
        if conn is None:
            conn = await _spawn_and_connect(socket_path)
        reader, writer = conn

        try:
            hello = await _send_request(
                reader,
                writer,
                "hello",
                {"client_version": __version__},
            )
            if hello.get("daemon_version") != __version__:
                if attempt == 1:
                    raise DaemonError(
                        f"version mismatch persists: daemon={hello.get('daemon_version')!r} "
                        f"client={__version__!r}",
                    )
                # Ask the stale daemon to exit, wait for it to release the
                # socket, then loop and let _spawn_and_connect bring up a
                # fresh one at the current version.
                try:
                    await _send_request(reader, writer, "shutdown", {})
                except DaemonError:
                    pass
                await _close(writer)
                await _wait_socket_gone(socket_path, SHUTDOWN_DRAIN_TIMEOUT)
                continue

            return await _send_request(reader, writer, method, params)
        finally:
            await _close(writer)

    raise DaemonUnavailable("daemon connect/respawn loop exhausted")


def call_sync(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync wrapper used from Click commands."""
    return asyncio.run(call(method, params))
