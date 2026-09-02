from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from noirdoc import cli as cli_module
from noirdoc import namespace as ns_module
from noirdoc import sdk as sdk_module
from noirdoc.cli import main
from noirdoc.namespace import Namespace


def _redirect_namespace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "namespaces"
    monkeypatch.setattr(ns_module, "DEFAULT_NAMESPACE_ROOT", root)
    monkeypatch.setattr(cli_module, "DEFAULT_NAMESPACE_ROOT", root)
    return root


def test_ns_summary_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_namespace_root(monkeypatch, tmp_path)

    ns = Namespace("demo")
    mapper = ns.load()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Lisa Schmidt", "PERSON")
    mapper.get_or_create("max@test.de", "EMAIL")
    ns.save(mapper)

    result = CliRunner().invoke(main, ["ns", "summary", "demo"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "namespace": "demo",
        "total_entities": 3,
        "by_type": {"PERSON": 2, "EMAIL": 1},
    }
    assert "Max Müller" not in result.output
    assert "max@test.de" not in result.output


def test_ns_summary_missing_namespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_namespace_root(monkeypatch, tmp_path)

    result = CliRunner().invoke(main, ["ns", "summary", "nope"])
    assert result.exit_code == 1
    assert "Namespace 'nope' does not exist." in result.output


def test_ns_show_requires_unsafe_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ns show must not print original values without --unsafe."""
    _redirect_namespace_root(monkeypatch, tmp_path)

    ns = Namespace("demo")
    mapper = ns.load()
    mapper.get_or_create("Anna Müller", "PERSON")
    ns.save(mapper)

    result = CliRunner().invoke(main, ["ns", "show", "demo"])
    assert result.exit_code == 2
    assert "Anna Müller" not in result.output
    assert "--unsafe" in result.output


def test_ns_show_unsafe_prints_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_namespace_root(monkeypatch, tmp_path)

    ns = Namespace("demo")
    mapper = ns.load()
    mapper.get_or_create("Anna Müller", "PERSON")
    ns.save(mapper)

    result = CliRunner().invoke(main, ["ns", "show", "demo", "--unsafe"])
    assert result.exit_code == 0, result.output
    assert "Anna Müller" in result.output


def test_redact_oversized_xlsx_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue #13: an archive over the 200 MB zip-safety cap used to be written back
    to the output path unmodified and reported as '0 entities'. The CLI must
    surface the reason, exit non-zero and write no output file."""
    from openpyxl import Workbook

    from tests.file_analysis.xlsx_helpers import SubstringDetector, rewrite_zip, workbook_bytes

    wb = Workbook()
    ws = wb.active
    ws.append(["Name"])
    ws.append(["Anna Mueller"])
    wb.properties.creator = ""
    # Pad the archive so the central directory declares > 200 MB uncompressed.
    data = rewrite_zip(workbook_bytes(wb), {"xl/media/padding.bin": b"A" * (201 * 1024 * 1024)})
    inp = tmp_path / "kunden.xlsx"
    inp.write_bytes(data)

    # No model loading; libmagic availability varies, so pin the MIME too.
    async def _fake_ensure_detector(self: sdk_module.Redactor) -> SubstringDetector:
        return SubstringDetector({})

    monkeypatch.setattr(sdk_module.Redactor, "_ensure_detector", _fake_ensure_detector)
    monkeypatch.setattr(sdk_module, "_detect_mime", lambda path, content: sdk_module._XLSX_MIME)

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        main,
        ["redact", "--no-daemon", str(inp), "--output-dir", str(out_dir)],
        catch_exceptions=False,
    )

    assert result.exit_code == 1, result.output
    assert "XLSX analysis failed for kunden.xlsx" in result.output
    assert "bytes uncompressed" in result.output
    assert not out_dir.exists() or not list(out_dir.iterdir())


def test_choose_output_path_drops_input_directory_components(tmp_path: Path) -> None:
    """Crafted input paths must not route output outside --output-dir."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    crafted = tmp_path / "in" / ".." / "etc" / "passwd-fake"
    crafted.parent.mkdir(parents=True, exist_ok=True)
    crafted.touch()
    chosen = cli_module._choose_output_path(
        crafted,
        output=None,
        output_dir=out_dir,
        reconstructed=True,
    )
    assert chosen.is_relative_to(out_dir.resolve())


def test_choose_output_path_refuses_namespace_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refuse to overwrite anything inside the namespaces directory."""
    namespaces_root = tmp_path / "namespaces"
    namespaces_root.mkdir()
    monkeypatch.setattr(cli_module, "DEFAULT_NAMESPACE_ROOT", namespaces_root)

    target = namespaces_root / "demo" / "key"
    with pytest.raises(click.ClickException, match="namespace store"):
        cli_module._choose_output_path(
            tmp_path / "vertrag.txt",
            output=target,
            output_dir=None,
            reconstructed=True,
        )
