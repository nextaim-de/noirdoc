"""When the daemon path fails, the CLI must fall back to in-process redaction."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from noirdoc import cli as cli_module
from noirdoc import sdk as sdk_module
from noirdoc.cli import main
from noirdoc.daemon import client as daemon_client
from noirdoc.sdk import RedactionResult


def _make_fake_redact_file(out_bytes: bytes = b"REDACTED"):
    def _fake(self, input_path, *, output=None, language=None):
        return RedactionResult(
            input_path=Path(input_path),
            output_bytes=out_bytes,
            entity_count=1,
            entity_types={"PERSON": 1},
            mime_type="text/plain",
            reconstructed=False,
        )

    return _fake


def test_cli_falls_back_when_daemon_unavailable(monkeypatch, tmp_path: Path):
    """call_sync raises DaemonUnavailable → CLI prints fallback warning and runs in-process."""

    def fake_call_sync(method, params=None):
        raise daemon_client.DaemonUnavailable("simulated: daemon not running")

    # Stub the daemon RPC.
    monkeypatch.setattr(
        "noirdoc.daemon.client.call_sync",
        fake_call_sync,
    )
    # Stub the in-process Redactor.redact_file so we don't load real models.
    monkeypatch.setattr(
        sdk_module.Redactor,
        "redact_file",
        _make_fake_redact_file(b"REDACTED-LOCAL"),
    )

    inp = tmp_path / "input.txt"
    inp.write_text("Max Mueller lives in Berlin.")

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        main,
        ["redact", str(inp), "--output-dir", str(out_dir)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "daemon unavailable" in result.output
    assert "1 entities" in result.output
    written = list(out_dir.glob("*"))
    assert len(written) == 1
    assert written[0].read_bytes() == b"REDACTED-LOCAL"


def test_cli_skips_daemon_when_no_daemon_flag_set(monkeypatch, tmp_path: Path):
    """--no-daemon must skip the daemon entirely (no DaemonUnavailable warning)."""
    call_count = {"n": 0}

    def fake_call_sync(method, params=None):
        call_count["n"] += 1
        raise daemon_client.DaemonUnavailable("should not be called")

    monkeypatch.setattr(
        "noirdoc.daemon.client.call_sync",
        fake_call_sync,
    )
    monkeypatch.setattr(
        sdk_module.Redactor,
        "redact_file",
        _make_fake_redact_file(),
    )

    inp = tmp_path / "input.txt"
    inp.write_text("hello")
    out_dir = tmp_path / "out"

    result = CliRunner().invoke(
        main,
        ["redact", "--no-daemon", str(inp), "--output-dir", str(out_dir)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "daemon unavailable" not in result.output.lower()
    assert call_count["n"] == 0


def test_cli_skips_daemon_when_env_var_set(monkeypatch, tmp_path: Path):
    """NOIRDOC_NO_DAEMON=1 also skips the daemon."""
    call_count = {"n": 0}

    def fake_call_sync(method, params=None):
        call_count["n"] += 1
        raise daemon_client.DaemonUnavailable("should not be called")

    monkeypatch.setenv("NOIRDOC_NO_DAEMON", "1")
    monkeypatch.setattr(
        "noirdoc.daemon.client.call_sync",
        fake_call_sync,
    )
    monkeypatch.setattr(
        sdk_module.Redactor,
        "redact_file",
        _make_fake_redact_file(),
    )

    inp = tmp_path / "input.txt"
    inp.write_text("hello")
    out_dir = tmp_path / "out"

    result = CliRunner().invoke(
        main,
        ["redact", str(inp), "--output-dir", str(out_dir)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert call_count["n"] == 0
    assert "daemon unavailable" not in result.output.lower()


def test_cli_module_uses_daemon_imports(monkeypatch):
    """Smoke test: the cli module exposes the helper used by tests."""
    assert hasattr(cli_module, "_redact_via_daemon")
    assert hasattr(cli_module, "_redact_in_process")
