from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner

from noirdoc import cli as cli_module
from noirdoc import namespace as ns_module
from noirdoc import sdk as sdk_module
from noirdoc.cli import main
from noirdoc.namespace import Namespace
from noirdoc.sdk import RedactionResult

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


def _install_stub_redactor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reconstructed: bool,
    reason: str | None = None,
) -> None:
    """Replace cli.Redactor with a stub that skips models and forces an outcome."""

    def fake_redact_file(path: Path, *, language: str | None = None) -> RedactionResult:
        if reconstructed:
            return RedactionResult(
                input_path=path,
                output_bytes=b"PK\x03\x04 fake docx bytes",
                entity_count=2,
                entity_types={"PERSON": 2},
                mime_type=DOCX_MIME,
                reconstructed=True,
            )
        return RedactionResult(
            input_path=path,
            output_bytes=b"masked plain text <<PERSON_1>>",
            entity_count=2,
            entity_types={"PERSON": 2},
            mime_type="text/plain",
            reconstructed=False,
            reason=reason,
        )

    class StubRedactor:
        def __init__(self, **kwargs: Any) -> None:
            self.mapper = SimpleNamespace(entity_count=0)

        def redact_file(self, path: Path, *, language: str | None = None) -> RedactionResult:
            return fake_redact_file(path, language=language)

    monkeypatch.setattr(cli_module, "Redactor", StubRedactor)


def test_redact_fallback_redirects_explicit_output_to_txt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """-o out.docx must not receive UTF-8 text when reconstruction fails."""
    _redirect_namespace_root(monkeypatch, tmp_path)
    _install_stub_redactor(
        monkeypatch,
        reconstructed=False,
        reason=f"reconstruction failed for {DOCX_MIME}",
    )

    inp = tmp_path / "brief.docx"
    inp.write_bytes(b"PK\x03\x04 broken docx")
    out = tmp_path / "out.docx"

    result = CliRunner().invoke(main, ["redact", str(inp), "-o", str(out), "--no-daemon"])
    assert result.exit_code == 0, result.output

    txt = tmp_path / "out.txt"
    assert txt.read_text(encoding="utf-8") == "masked plain text <<PERSON_1>>"
    assert not out.exists(), "plain text must never land in the .docx path"
    # stderr names both paths and carries the reason.
    assert "out.txt" in result.stderr
    assert "out.docx" in result.stderr
    assert "reconstruction failed" in result.stderr
    # The success line points at the file that was actually written.
    assert str(txt) in result.output


def test_redact_fallback_without_output_warns_and_writes_txt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_namespace_root(monkeypatch, tmp_path)
    _install_stub_redactor(
        monkeypatch,
        reconstructed=False,
        reason="format application/pdf does not support in-place reconstruction",
    )

    inp = tmp_path / "vertrag.pdf"
    inp.write_bytes(b"%PDF-1.4 fake")

    result = CliRunner().invoke(main, ["redact", str(inp), "--no-daemon"])
    assert result.exit_code == 0, result.output

    txt = tmp_path / "vertrag_redacted.txt"
    assert txt.read_text(encoding="utf-8") == "masked plain text <<PERSON_1>>"
    assert "could not preserve the original format" in result.stderr
    assert "does not support in-place reconstruction" in result.stderr


def test_redact_reconstructed_keeps_explicit_output_and_stays_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_namespace_root(monkeypatch, tmp_path)
    _install_stub_redactor(monkeypatch, reconstructed=True)

    inp = tmp_path / "brief.docx"
    inp.write_bytes(b"PK\x03\x04 fine docx")
    out = tmp_path / "out.docx"

    result = CliRunner().invoke(main, ["redact", str(inp), "-o", str(out), "--no-daemon"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"PK\x03\x04 fake docx bytes"
    assert not (tmp_path / "out.txt").exists()
    assert result.stderr == ""


def test_redact_daemon_fallback_renames_and_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The daemon path must surface the same redirect + warning as in-process."""
    _redirect_namespace_root(monkeypatch, tmp_path)
    monkeypatch.delenv("NOIRDOC_NO_DAEMON", raising=False)

    reason = f"reconstruction failed for {DOCX_MIME}"

    def fake_call_sync(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert method == "redact"
        assert params is not None
        out_path = Path(params["output_path"])
        out_path.write_bytes(b"masked plain text <<PERSON_1>>")
        return {
            "output_path": str(out_path),
            "entity_count": 2,
            "entity_types": {"PERSON": 2},
            "mime_type": "text/plain",
            "reconstructed": False,
            "reason": reason,
            "namespace_size": None,
        }

    monkeypatch.setattr("noirdoc.daemon.client.call_sync", fake_call_sync)

    inp = tmp_path / "brief.docx"
    inp.write_bytes(b"PK\x03\x04 broken docx")
    out = tmp_path / "out.docx"

    result = CliRunner().invoke(main, ["redact", str(inp), "-o", str(out)])
    assert result.exit_code == 0, result.output

    txt = tmp_path / "out.txt"
    assert txt.read_text(encoding="utf-8") == "masked plain text <<PERSON_1>>"
    assert not out.exists()
    assert "out.txt" in result.stderr
    assert "out.docx" in result.stderr
    assert "reconstruction failed" in result.stderr


def test_choose_output_path_redirects_non_txt_output_on_fallback(tmp_path: Path) -> None:
    chosen = cli_module._choose_output_path(
        tmp_path / "brief.docx",
        output=tmp_path / "out.docx",
        output_dir=None,
        reconstructed=False,
    )
    assert chosen == (tmp_path / "out.txt").resolve()

    # An explicit .txt output is left untouched.
    chosen_txt = cli_module._choose_output_path(
        tmp_path / "brief.docx",
        output=tmp_path / "clean.txt",
        output_dir=None,
        reconstructed=False,
    )
    assert chosen_txt == (tmp_path / "clean.txt").resolve()


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
