"""asyncio Unix-socket daemon: serial redaction + cached models.

One daemon, one event loop, one in-flight redact at a time. Models are
cached at the daemon level so cold-start cost is paid once per session.
Per-request namespace state is loaded from disk and saved back, never
cached, to avoid coherence issues with concurrent CLI processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from noirdoc import __version__
from noirdoc.daemon import paths, spawn
from noirdoc.daemon.protocol import (
    ERR_BAD_REQUEST,
    ERR_INTERNAL,
    ERR_UNKNOWN_METHOD,
    ErrorPayload,
    HelloParams,
    HelloResult,
    RedactFileInput,
    RedactParams,
    RedactResult,
    RedactTextInput,
    Request,
    Response,
    ShutdownResult,
    StatusResult,
)

if TYPE_CHECKING:
    from noirdoc.detection.base import BaseDetector
    from noirdoc.detection.presidio_detector import PresidioDetector

DEFAULT_IDLE_SECONDS = 600  # 10 minutes
IDLE_CHECK_INTERVAL = 60.0
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 1
SUPPORTED_WARMUP_LANGUAGES = ("de", "en")

log = logging.getLogger("noirdoc.daemon")


def _setup_logging() -> None:
    paths.ensure_root_dir()
    handler = logging.handlers.RotatingFileHandler(
        paths.logfile_path(),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _idle_seconds() -> float:
    env = os.environ.get("NOIRDOC_DAEMON_IDLE_SECONDS")
    if env:
        try:
            return float(env)
        except ValueError:
            log.warning("ignoring invalid NOIRDOC_DAEMON_IDLE_SECONDS=%r", env)
    return float(DEFAULT_IDLE_SECONDS)


class DaemonState:
    """Mutable runtime state. One instance per daemon process."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.last_request_at: float | None = None
        self.total_requests = 0
        self.queue_depth = 0  # pending + in-flight redacts
        self.redact_lock = asyncio.Lock()
        self.shutdown_event = asyncio.Event()

        # Cached underlying detectors. EnsembleDetector wrappers are built
        # per-request so each request can pick its own score threshold and
        # detector subset.
        self._presidio_by_lang: dict[str, PresidioDetector] = {}
        # GlinerDetector when installed; typed Any so mypy permits the lazy
        # instantiation without dragging in the optional dependency.
        self._gliner: Any = None
        self._gliner_attempted = False
        self._gliner_model_name: str | None = None
        self._init_lock = asyncio.Lock()  # serialize lazy detector init

        self.models_loaded_event = asyncio.Event()

    @property
    def models_loaded(self) -> bool:
        return self.models_loaded_event.is_set()

    async def warmup(self) -> None:
        """Eagerly load every detector we expect to need.

        Failures are logged but never raised — a missing GLiNER means the
        ensemble degrades to Presidio-only, which is the same behaviour as
        in-process ``Redactor``.
        """
        from noirdoc.detection.model_manager import ensure_spacy_models

        for lang in SUPPORTED_WARMUP_LANGUAGES:
            try:
                await asyncio.to_thread(ensure_spacy_models, [lang])
                from noirdoc.detection.presidio_detector import PresidioDetector

                self._presidio_by_lang[lang] = PresidioDetector(languages=[lang])
                log.info("loaded presidio for language=%s", lang)
            except Exception:
                log.exception("presidio warmup failed for language=%s", lang)

        await self._load_gliner_if_available(
            "knowledgator/gliner-pii-edge-v1.0",
        )

        self.models_loaded_event.set()
        log.info("warmup complete")

    async def _load_gliner_if_available(self, model_name: str) -> None:
        if self._gliner_attempted:
            return
        self._gliner_attempted = True
        self._gliner_model_name = model_name
        try:
            from noirdoc.detection.gliner_detector import GlinerDetector
        except ImportError:
            log.info("gliner not installed; ensemble will use presidio only")
            return
        try:
            self._gliner = await asyncio.to_thread(
                GlinerDetector,
                model_name=model_name,
            )
            log.info("loaded gliner model=%s", model_name)
        except Exception:
            log.exception("gliner load failed for model=%s", model_name)

    async def get_detectors(
        self,
        language: str,
        choice: str,
        gliner_model: str,
    ) -> list[BaseDetector]:
        """Return cached detector instances for a request.

        Lazily loads anything warmup didn't already cover (e.g., a request
        for a language not in ``SUPPORTED_WARMUP_LANGUAGES``).
        """
        from noirdoc.detection.base import BaseDetector  # noqa: F401  (typing)

        out: list[BaseDetector] = []
        async with self._init_lock:
            if choice in ("presidio", "ensemble"):
                if language not in self._presidio_by_lang:
                    from noirdoc.detection.model_manager import ensure_spacy_models
                    from noirdoc.detection.presidio_detector import PresidioDetector

                    await asyncio.to_thread(ensure_spacy_models, [language])
                    self._presidio_by_lang[language] = PresidioDetector(
                        languages=[language],
                    )
                out.append(self._presidio_by_lang[language])

            if choice in ("gliner", "ensemble"):
                if not self._gliner_attempted or (self._gliner_model_name != gliner_model):
                    # Honor a request that wants a different GLiNER model than
                    # the one we warmed up with.
                    self._gliner_attempted = False
                    await self._load_gliner_if_available(gliner_model)
                if self._gliner is not None:
                    out.append(self._gliner)
                elif choice == "gliner":
                    raise RuntimeError(
                        "GLiNER is not installed (pip install 'noirdoc[full]')",
                    )

        return out


# -- request handlers --------------------------------------------------------


async def handle_hello(state: DaemonState, params: dict[str, Any]) -> dict[str, Any]:
    HelloParams.model_validate(params)  # validates client_version present
    return HelloResult(
        daemon_version=__version__,
        pid=os.getpid(),
        started_at=state.start_time,
    ).model_dump()


async def handle_status(state: DaemonState, params: dict[str, Any]) -> dict[str, Any]:
    return StatusResult(
        uptime_s=time.time() - state.start_time,
        models_loaded=state.models_loaded,
        last_request_at=state.last_request_at,
        queue_depth=state.queue_depth,
        total_requests=state.total_requests,
    ).model_dump()


async def handle_shutdown(
    state: DaemonState,
    params: dict[str, Any],
) -> dict[str, Any]:
    state.shutdown_event.set()
    return ShutdownResult().model_dump()


async def handle_redact(
    state: DaemonState,
    params: dict[str, Any],
) -> dict[str, Any]:
    from noirdoc.detection.ensemble import EnsembleDetector
    from noirdoc.sdk import build_redactor

    parsed = RedactParams.model_validate(params)

    state.queue_depth += 1
    try:
        async with state.redact_lock:
            state.last_request_at = time.time()
            state.total_requests += 1
            t0 = time.monotonic()

            detectors = await state.get_detectors(
                parsed.language,
                parsed.detector,
                parsed.gliner_model,
            )
            ensemble = EnsembleDetector(
                detectors=detectors,
                score_threshold=parsed.score_threshold,
            )

            redactor = build_redactor(
                ensemble=ensemble,
                namespace=parsed.namespace,
                namespace_root=parsed.namespace_root,
                language=parsed.language,
                detector=parsed.detector,  # type: ignore[arg-type]
                score_threshold=parsed.score_threshold,
                gliner_model=parsed.gliner_model,
            )

            if isinstance(parsed.input, RedactTextInput):
                pseudonymized, entities = await redactor.aredact_text_detailed(
                    parsed.input.value,
                    parsed.language,
                )
                entity_types: dict[str, int] = {}
                for e in entities:
                    entity_types[e.entity_type] = entity_types.get(e.entity_type, 0) + 1
                result = RedactResult(
                    redacted_text=pseudonymized,
                    entity_count=len(entities),
                    entity_types=entity_types,
                    namespace_size=redactor.mapper.entity_count,
                )
            else:
                assert isinstance(parsed.input, RedactFileInput)
                in_path = Path(parsed.input.path)
                file_result = await redactor.aredact_file(
                    in_path,
                    language=parsed.language,
                )
                if parsed.output_path:
                    out_path = Path(parsed.output_path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(file_result.output_bytes)
                    output_path_str: str | None = str(out_path)
                else:
                    output_path_str = None
                result = RedactResult(
                    output_path=output_path_str,
                    entity_count=file_result.entity_count,
                    entity_types=file_result.entity_types,
                    mime_type=file_result.mime_type,
                    reconstructed=file_result.reconstructed,
                    namespace_size=redactor.mapper.entity_count,
                )

            duration_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "redact ns=%s lang=%s detector=%s entities=%d ms=%d",
                parsed.namespace,
                parsed.language,
                parsed.detector,
                result.entity_count,
                duration_ms,
            )
            return result.model_dump()
    finally:
        state.queue_depth = max(state.queue_depth - 1, 0)


HANDLERS: dict[str, Any] = {
    "hello": handle_hello,
    "status": handle_status,
    "shutdown": handle_shutdown,
    "redact": handle_redact,
}


# -- connection plumbing -----------------------------------------------------


def _serialize(response: Response) -> bytes:
    return (json.dumps(response.model_dump(exclude_none=True), ensure_ascii=False) + "\n").encode(
        "utf-8",
    )


async def _dispatch(state: DaemonState, raw_line: bytes) -> Response:
    try:
        payload = json.loads(raw_line.decode("utf-8"))
        request = Request.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        return Response(
            id="",
            error=ErrorPayload(code=ERR_BAD_REQUEST, message=str(exc)),
        )

    handler = HANDLERS.get(request.method)
    if handler is None:
        return Response(
            id=request.id,
            error=ErrorPayload(
                code=ERR_UNKNOWN_METHOD,
                message=f"unknown method: {request.method!r}",
            ),
        )

    try:
        result = await handler(state, request.params)
    except ValidationError as exc:
        return Response(
            id=request.id,
            error=ErrorPayload(code=ERR_BAD_REQUEST, message=str(exc)),
        )
    except Exception as exc:
        log.exception("handler %s raised", request.method)
        return Response(
            id=request.id,
            error=ErrorPayload(code=ERR_INTERNAL, message=str(exc)),
        )

    return Response(id=request.id, result=result)


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: DaemonState,
) -> None:
    try:
        while not state.shutdown_event.is_set():
            line = await reader.readline()
            if not line:
                return  # client closed
            response = await _dispatch(state, line)
            writer.write(_serialize(response))
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, BrokenPipeError):
        pass
    except Exception:
        log.exception("connection handler crashed")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _idle_watcher(state: DaemonState) -> None:
    idle = _idle_seconds()
    while not state.shutdown_event.is_set():
        try:
            await asyncio.wait_for(
                state.shutdown_event.wait(),
                timeout=IDLE_CHECK_INTERVAL,
            )
            return
        except TimeoutError:
            pass
        if state.last_request_at is None:
            # No request yet — measure idleness from start_time.
            elapsed = time.time() - state.start_time
        else:
            elapsed = time.time() - state.last_request_at
        if elapsed > idle and not state.redact_lock.locked():
            log.info("idle shutdown after %.0fs of inactivity", elapsed)
            state.shutdown_event.set()
            return


# -- bootstrap ---------------------------------------------------------------


async def _async_main() -> None:
    _setup_logging()
    paths.ensure_root_dir()
    spawn.cleanup_stale_socket()

    existing_pid = spawn.read_pidfile()
    if existing_pid is not None and spawn.is_pid_alive(existing_pid):
        log.info("another daemon is already running (pid=%d), exiting", existing_pid)
        return

    spawn.write_pidfile(os.getpid())
    state = DaemonState()
    log.info("daemon starting pid=%d version=%s", os.getpid(), __version__)

    warmup_task = asyncio.create_task(state.warmup(), name="warmup")
    idle_task = asyncio.create_task(_idle_watcher(state), name="idle-watcher")

    sock_path = paths.socket_path()
    server = await asyncio.start_unix_server(
        lambda r, w: _handle_connection(r, w, state),
        path=str(sock_path),
    )
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        log.warning("could not chmod socket %s", sock_path)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, state.shutdown_event.set)
        except NotImplementedError:
            pass  # Windows; we don't ship there but be defensive.

    log.info("listening on %s", sock_path)
    try:
        async with server:
            await state.shutdown_event.wait()
    finally:
        log.info("draining and shutting down")
        idle_task.cancel()
        warmup_task.cancel()
        for task in (idle_task, warmup_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        spawn.remove_pidfile()
        log.info("daemon stopped")


def main() -> None:
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
