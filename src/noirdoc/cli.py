"""Command-line interface for noirdoc."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noirdoc import __version__
from noirdoc.namespace import DEFAULT_NAMESPACE_ROOT, Namespace, list_namespaces
from noirdoc.sdk import Redactor


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
def redact(
    inputs: tuple[Path, ...],
    output: Path | None,
    output_dir: Path | None,
    namespace: str | None,
    language: str,
    detector: str,
    score_threshold: float,
    verbose: bool,
) -> None:
    """Redact PII in one or more files."""
    files = _expand_inputs(inputs)
    if not files:
        click.echo("No input files found.", err=True)
        sys.exit(1)

    if output and len(files) > 1:
        click.echo("--output only works with a single input; use --output-dir.", err=True)
        sys.exit(2)

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

    sys.exit(0 if success else 1)


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
