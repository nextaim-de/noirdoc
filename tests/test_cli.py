from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
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


def test_ns_show_requires_unsafe_flag(monkeypatch, tmp_path: Path):
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


def test_ns_show_unsafe_prints_mapping(monkeypatch, tmp_path: Path):
    _redirect_namespace_root(monkeypatch, tmp_path)

    ns = Namespace("demo")
    mapper = ns.load()
    mapper.get_or_create("Anna Müller", "PERSON")
    ns.save(mapper)

    result = CliRunner().invoke(main, ["ns", "show", "demo", "--unsafe"])
    assert result.exit_code == 0, result.output
    assert "Anna Müller" in result.output


def test_choose_output_path_drops_input_directory_components(tmp_path: Path):
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


def test_choose_output_path_refuses_namespace_store(monkeypatch, tmp_path: Path):
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
