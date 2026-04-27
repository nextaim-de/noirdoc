"""Command-line interface for noirdoc."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from noirdoc import __version__
from noirdoc.namespace import DEFAULT_NAMESPACE_ROOT, Namespace, list_namespaces
from noirdoc.sdk import Redactor


def _daemon_disabled() -> bool:
    return os.environ.get("NOIRDOC_NO_DAEMON", "").strip().lower() in {"1", "true", "yes"}


@click.group()
@click.version_option(__version__, prog_name="noirdoc")
def main() -> None:
    """Local PII redaction and pseudonymization for documents."""


@main.command()
@click.argument("inputs", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file (single input).")
@click.option("--output-dir", type=click.Path(path_type=Path), help="Output directory (batch).")
@click.option("--namespace", help="Persistent namespace for reversible pseudonyms.")
@click.option(
    "--language",
    "-l",
    default="de",
    type=click.Choice(["de", "en"]),
    show_default=True,
)
@click.option(
    "--detector",
    default="ensemble",
    type=click.Choice(["presidio", "gliner", "ensemble"]),
    show_default=True,
)
@click.option("--score-threshold", type=float, default=0.5, show_default=True)
@click.option("-v", "--verbose", is_flag=True, help="Print per-file details.")
@click.option(
    "--no-daemon",
    is_flag=True,
    help="Skip the daemon and run redaction in-process (each call reloads models).",
)
def redact(
    inputs: tuple[Path, ...],
    output: Path | None,
    output_dir: Path | None,
    namespace: str | None,
    language: str,
    detector: str,
    score_threshold: float,
    verbose: bool,
    no_daemon: bool,
) -> None:
    """Redact PII in one or more files."""
    files = _expand_inputs(inputs)
    if not files:
        click.echo("No input files found.", err=True)
        sys.exit(1)

    if output and len(files) > 1:
        click.echo("--output only works with a single input; use --output-dir.", err=True)
        sys.exit(2)

    use_daemon = not (no_daemon or _daemon_disabled())

    if use_daemon:
        success, last_namespace_size, switched = _redact_via_daemon(
            files,
            output=output,
            output_dir=output_dir,
            namespace=namespace,
            language=language,
            detector=detector,
            score_threshold=score_threshold,
            verbose=verbose,
        )
        if switched is None:
            # Daemon handled every file successfully.
            if namespace and last_namespace_size is not None:
                click.echo(
                    f"Namespace {namespace!r}: {last_namespace_size} unique entities total",
                )
            sys.exit(0 if success else 1)
        # Fall through with the remaining files handled in-process.
        files = switched
    else:
        success = 0

    success += _redact_in_process(
        files,
        output=output,
        output_dir=output_dir,
        namespace=namespace,
        language=language,
        detector=detector,
        score_threshold=score_threshold,
        verbose=verbose,
    )
    sys.exit(0 if success else 1)


def _redact_via_daemon(
    files: list[Path],
    *,
    output: Path | None,
    output_dir: Path | None,
    namespace: str | None,
    language: str,
    detector: str,
    score_threshold: float,
    verbose: bool,
) -> tuple[int, int | None, list[Path] | None]:
    """Redact via the daemon. Returns (successes, last_namespace_size, fallback_remaining).

    ``fallback_remaining`` is ``None`` on full success, or a list of files
    not yet processed when the daemon failed and the caller should fall
    back to in-process redaction.
    """
    from noirdoc.daemon.client import DaemonError, DaemonUnavailable, call_sync

    success = 0
    last_namespace_size: int | None = None

    for i, path in enumerate(files):
        out_path = _choose_output_path(
            path,
            output=output,
            output_dir=output_dir,
            reconstructed=True,  # daemon decides; we'll rename if it says reconstructed=False
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        params = {
            "namespace": namespace,
            "language": language,
            "detector": detector,
            "score_threshold": score_threshold,
            "input": {"type": "file", "path": str(path.resolve())},
            "output_path": str(out_path.resolve()),
        }
        try:
            result = call_sync("redact", params)
        except DaemonUnavailable as exc:
            click.echo(
                f"noirdoc: daemon unavailable ({exc}); running locally for the rest.",
                err=True,
            )
            return success, last_namespace_size, list(files[i:])
        except DaemonError as exc:
            click.echo(
                f"noirdoc: daemon error ({exc}); running locally for the rest.",
                err=True,
            )
            return success, last_namespace_size, list(files[i:])
        except Exception as exc:  # connection died, etc.
            click.echo(
                f"noirdoc: daemon failed mid-request ({exc}); running locally for the rest.",
                err=True,
            )
            return success, last_namespace_size, list(files[i:])

        # If the daemon couldn't reconstruct the file, the output it wrote
        # is plain text — rename to .txt to match in-process behaviour.
        if not result.get("reconstructed", False):
            corrected = _choose_output_path(
                path,
                output=output,
                output_dir=output_dir,
                reconstructed=False,
            )
            if corrected != out_path:
                Path(out_path).rename(corrected)
                out_path = corrected

        last_namespace_size = result.get("namespace_size") or last_namespace_size

        if verbose:
            types = ", ".join(f"{k}={v}" for k, v in sorted(result.get("entity_types", {}).items()))
            click.echo(
                f"{path.name}: {result['entity_count']} entities [{types}] -> {out_path}",
            )
        else:
            click.echo(f"{path.name}: {result['entity_count']} entities -> {out_path}")
        success += 1

    return success, last_namespace_size, None


def _redact_in_process(
    files: list[Path],
    *,
    output: Path | None,
    output_dir: Path | None,
    namespace: str | None,
    language: str,
    detector: str,
    score_threshold: float,
    verbose: bool,
) -> int:
    if not files:
        return 0

    r = Redactor(
        namespace=namespace,
        language=language,
        detector=detector,  # type: ignore[arg-type]
        score_threshold=score_threshold,
    )

    success = 0
    for path in files:
        try:
            result = r.redact_file(path, language=language)
        except Exception as exc:
            click.echo(f"{path.name}: {exc}", err=True)
            continue

        out_path = _choose_output_path(
            path,
            output=output,
            output_dir=output_dir,
            reconstructed=result.reconstructed,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(result.output_bytes)

        if verbose:
            types = ", ".join(f"{k}={v}" for k, v in sorted(result.entity_types.items()))
            click.echo(f"{path.name}: {result.entity_count} entities [{types}] -> {out_path}")
        else:
            click.echo(f"{path.name}: {result.entity_count} entities -> {out_path}")
        success += 1

    if namespace:
        click.echo(f"Namespace {namespace!r}: {r.mapper.entity_count} unique entities total")

    return success


@main.command()
@click.argument("input", required=False, type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file.")
@click.option("--namespace", required=True, help="Namespace holding the mapping.")
def reveal(input: Path | None, output: Path | None, namespace: str) -> None:
    """Restore original values from pseudonyms using a namespace.

    With no INPUT, reads text from stdin and writes to stdout.
    With an INPUT file, writes reidentified bytes (DOCX/XLSX/plain) to OUTPUT.
    PDF/PPTX/images are not roundtrippable; the command warns and copies the
    original bytes.
    """
    ns = Namespace(namespace)
    if not ns.exists():
        click.echo(f"Namespace {namespace!r} does not exist.", err=True)
        sys.exit(1)

    r = Redactor(namespace=namespace)

    if input is None:
        text = sys.stdin.read()
        sys.stdout.write(r.reveal_text(text))
        return

    revealed = r.reveal_file(input, output=output)
    if revealed is None:
        click.echo(
            f"Cannot reveal {input.name}: format not supported for roundtrip. "
            "Original bytes unchanged.",
            err=True,
        )
        if output:
            Path(output).write_bytes(input.read_bytes())
        sys.exit(2)
    click.echo(f"{input.name}: revealed -> {output or '(bytes returned)'}")


@main.command()
@click.argument("pseudonym")
@click.option("--namespace", required=True)
def lookup(pseudonym: str, namespace: str) -> None:
    """Look up the original value behind a pseudonym token."""
    r = Redactor(namespace=namespace)
    original = r.lookup(pseudonym)
    if original is None:
        click.echo(f"{pseudonym}: not found", err=True)
        sys.exit(1)
    click.echo(original)


@main.group()
def ns() -> None:
    """Manage persistent namespaces."""


@ns.command("list")
def ns_list() -> None:
    """List all namespaces under ~/.noirdoc/namespaces/."""
    names = list_namespaces()
    if not names:
        click.echo(f"No namespaces found in {DEFAULT_NAMESPACE_ROOT}")
        return
    for n in names:
        click.echo(n)


@ns.command("show")
@click.argument("namespace")
def ns_show(namespace: str) -> None:
    """Print the mapping summary for NAMESPACE as JSON."""
    ns_obj = Namespace(namespace)
    if not ns_obj.exists():
        click.echo(f"Namespace {namespace!r} does not exist.", err=True)
        sys.exit(1)
    mapper = ns_obj.load()
    click.echo(json.dumps(mapper.get_mapping_summary(), indent=2, ensure_ascii=False))


@ns.command("summary")
@click.argument("namespace")
def ns_summary(namespace: str) -> None:
    """Print counts-only summary for NAMESPACE as JSON.

    Safe alternative to ``ns show`` when stdout may be captured by a
    wrapper, transcript, or log pipeline — original values never appear
    in the output.
    """
    ns_obj = Namespace(namespace)
    if not ns_obj.exists():
        click.echo(f"Namespace {namespace!r} does not exist.", err=True)
        sys.exit(1)
    mapper = ns_obj.load()
    summary = {"namespace": namespace, **mapper.get_counts_summary()}
    click.echo(json.dumps(summary, indent=2, ensure_ascii=False))


@ns.command("delete")
@click.argument("namespace")
@click.confirmation_option(prompt="Delete namespace and discard all mappings?")
def ns_delete(namespace: str) -> None:
    """Delete a namespace directory (irreversible)."""
    ns_obj = Namespace(namespace)
    if not ns_obj.exists():
        click.echo(f"Namespace {namespace!r} does not exist.", err=True)
        sys.exit(1)
    ns_obj.delete()
    click.echo(f"Deleted {namespace!r}")


@main.group("daemon")
def daemon_group() -> None:
    """Inspect and control the long-lived noirdoc daemon."""


@daemon_group.command("status")
def daemon_status() -> None:
    """Print whether a daemon is running and its current stats."""
    from noirdoc.daemon import paths as daemon_paths
    from noirdoc.daemon import spawn as daemon_spawn
    from noirdoc.daemon.client import DaemonError, call_sync

    pid = daemon_spawn.read_pidfile()
    if pid is None or not daemon_spawn.is_pid_alive(pid):
        click.echo(json.dumps({"running": False}, indent=2))
        return

    try:
        status = call_sync("status")
    except DaemonError as exc:
        click.echo(
            json.dumps({"running": True, "pid": pid, "error": str(exc)}, indent=2),
            err=True,
        )
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                "running": True,
                "pid": pid,
                "socket": str(daemon_paths.socket_path()),
                **status,
            },
            indent=2,
        ),
    )


@daemon_group.command("stop")
def daemon_stop() -> None:
    """Send SIGTERM to the daemon and wait for it to exit."""
    from noirdoc.daemon import spawn as daemon_spawn

    pid = daemon_spawn.read_pidfile()
    if pid is None or not daemon_spawn.is_pid_alive(pid):
        click.echo("daemon is not running")
        return

    if daemon_spawn.stop_daemon():
        click.echo(f"daemon (pid={pid}) stopped")
    else:
        click.echo(f"daemon (pid={pid}) did not stop in time", err=True)
        sys.exit(1)


@daemon_group.command("restart")
@click.pass_context
def daemon_restart(ctx: click.Context) -> None:
    """Stop the daemon if running. Next ``redact`` will auto-spawn a fresh one."""
    ctx.invoke(daemon_stop)
    click.echo("(daemon will auto-spawn on next redact)")


@daemon_group.command("logs")
@click.option("-n", "--lines", type=int, default=50, show_default=True)
def daemon_logs(lines: int) -> None:
    """Print the tail of the daemon log file."""
    from noirdoc.daemon import paths as daemon_paths

    log = daemon_paths.logfile_path()
    if not log.exists():
        click.echo("no log file yet", err=True)
        sys.exit(1)
    text = log.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines()[-lines:]:
        click.echo(line)


@main.group()
def models() -> None:
    """Manage local detection models."""


@models.command("pull")
@click.option("--language", "-l", multiple=True, default=["de", "en"], show_default=True)
@click.option("--gliner/--no-gliner", default=True, show_default=True)
@click.option(
    "--gliner-model",
    default="knowledgator/gliner-pii-edge-v1.0",
    show_default=True,
)
def models_pull(language: tuple[str, ...], gliner: bool, gliner_model: str) -> None:
    """Download spaCy models (and optionally GLiNER weights)."""
    from noirdoc.detection.model_manager import ensure_spacy_models

    click.echo(f"Downloading spaCy models for: {', '.join(language)}")
    ensure_spacy_models(list(language))

    if gliner:
        click.echo(f"Loading GLiNER model: {gliner_model}")
        try:
            from noirdoc.detection.gliner_detector import GlinerDetector

            GlinerDetector(model_name=gliner_model)
        except ImportError:
            click.echo(
                "GLiNER not installed. Install with: pip install 'noirdoc[full]'",
                err=True,
            )
            sys.exit(1)

    click.echo("Models ready.")


# -- helpers ------------------------------------------------------------------


def _expand_inputs(inputs: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for p in inputs:
        if p.is_dir():
            files.extend(sorted(q for q in p.iterdir() if q.is_file()))
        elif p.is_file():
            files.append(p)
    return files


def _choose_output_path(
    input_path: Path,
    *,
    output: Path | None,
    output_dir: Path | None,
    reconstructed: bool,
) -> Path:
    if output:
        return output
    parent = output_dir or input_path.parent
    if reconstructed:
        return parent / f"{input_path.stem}_redacted{input_path.suffix}"
    return parent / f"{input_path.stem}_redacted.txt"


if __name__ == "__main__":
    main()
