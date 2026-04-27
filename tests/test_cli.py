from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from noirdoc import cli as cli_module
from noirdoc import namespace as ns_module
from noirdoc.cli import main
from noirdoc.namespace import Namespace


def _redirect_namespace_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "namespaces"
    monkeypatch.setattr(ns_module, "DEFAULT_NAMESPACE_ROOT", root)
    monkeypatch.setattr(cli_module, "DEFAULT_NAMESPACE_ROOT", root)
    return root


def test_ns_summary_happy_path(monkeypatch, tmp_path: Path):
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


def test_ns_summary_missing_namespace(monkeypatch, tmp_path: Path):
    _redirect_namespace_root(monkeypatch, tmp_path)

    result = CliRunner().invoke(main, ["ns", "summary", "nope"])
    assert result.exit_code == 1
    assert "Namespace 'nope' does not exist." in result.output
