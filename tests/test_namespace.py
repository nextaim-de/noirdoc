"""Tests for persistent namespaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from noirdoc.namespace import Namespace, list_namespaces
from noirdoc.pseudonymization.mapper import PseudonymMapper


def test_create_namespace_generates_key(tmp_path: Path):
    ns = Namespace("demo", root=tmp_path)
    assert not ns.exists()

    mapper = ns.load()
    assert isinstance(mapper, PseudonymMapper)
    assert mapper.entity_count == 0
    assert ns.exists()
    assert ns.key_path.is_file()
    # Key file should be 0600
    mode = ns.key_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_and_load_roundtrip(tmp_path: Path):
    ns = Namespace("demo", root=tmp_path)
    mapper = ns.load()
    mapper.get_or_create("John Smith", "PERSON")
    mapper.get_or_create("john@example.com", "EMAIL")
    ns.save(mapper)

    # New Namespace instance loads the same state
    ns2 = Namespace("demo", root=tmp_path)
    restored = ns2.load()
    assert restored.get_mapping_summary() == mapper.get_mapping_summary()


def test_consistent_pseudonyms_across_sessions(tmp_path: Path):
    ns1 = Namespace("demo", root=tmp_path)
    m1 = ns1.load()
    assert m1.get_or_create("John Smith", "PERSON") == "<<PERSON_1>>"
    ns1.save(m1)

    ns2 = Namespace("demo", root=tmp_path)
    m2 = ns2.load()
    # Re-encountering the same entity returns the same pseudonym
    assert m2.get_or_create("John Smith", "PERSON") == "<<PERSON_1>>"
    # New entity continues the counter
    assert m2.get_or_create("Jane Doe", "PERSON") == "<<PERSON_2>>"


def test_delete_namespace(tmp_path: Path):
    ns = Namespace("demo", root=tmp_path)
    mapper = ns.load()
    mapper.get_or_create("John Smith", "PERSON")
    ns.save(mapper)
    assert ns.exists()

    ns.delete()
    assert not ns.exists()


def test_list_namespaces(tmp_path: Path):
    assert list_namespaces(root=tmp_path) == []

    Namespace("alpha", root=tmp_path).load()
    Namespace("beta", root=tmp_path).load()

    assert list_namespaces(root=tmp_path) == ["alpha", "beta"]


def test_mapper_to_dict_roundtrip():
    mapper = PseudonymMapper()
    mapper.get_or_create("John Smith", "PERSON")
    mapper.get_or_create("john@example.com", "EMAIL")

    data = mapper.to_dict()
    restored = PseudonymMapper.from_dict(data)

    assert restored.get_mapping_summary() == mapper.get_mapping_summary()
    # Counter continues from where it left off
    assert restored.get_or_create("Jane Doe", "PERSON") == "<<PERSON_2>>"


def test_corrupt_key_raises(tmp_path: Path):
    ns = Namespace("demo", root=tmp_path)
    mapper = ns.load()
    mapper.get_or_create("John", "PERSON")
    ns.save(mapper)

    # Overwrite the key with garbage
    ns.key_path.write_bytes(b"not-a-valid-fernet-key" + b"=" * 20)

    ns2 = Namespace("demo", root=tmp_path)
    with pytest.raises(RuntimeError, match="decrypt"):
        ns2.load()
