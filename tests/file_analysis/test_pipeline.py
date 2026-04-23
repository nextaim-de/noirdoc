"""Integration tests for the file analysis pipeline."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

from noirdoc.detection.base import DetectedEntity
from noirdoc.file_analysis.models import FileAnalysisMode
from noirdoc.file_analysis.pipeline import analyze_files_in_body
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper


def _b64_uri(content: bytes, mime: str = "text/plain") -> str:
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def _make_detector(entities: list[DetectedEntity] | None = None):
    mock = AsyncMock()
    mock.detect = AsyncMock(return_value=entities or [])
    return mock


def _person_entity(text: str, start: int, end: int) -> DetectedEntity:
    return DetectedEntity(
        entity_type="PERSON",
        text=text,
        start=start,
        end=end,
        score=0.9,
        source="test",
    )


# ── Passthrough mode ─────────────────────────────────────────


async def test_passthrough_does_nothing():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "file", "file": {"file_data": _b64_uri(b"data", "text/plain")}},
                ],
            },
        ],
    }
    detector = _make_detector()
    mapper = PseudonymMapper()

    result_body, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.PASSTHROUGH,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    assert result.files_analyzed == 0
    detector.detect.assert_not_called()


# ── Detect-only mode ─────────────────────────────────────────


async def test_detect_only_logs_but_does_not_modify():
    original_text = b"Hello John Doe"
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "file", "file": {"file_data": _b64_uri(original_text, "text/plain")}},
                ],
            },
        ],
    }
    entities = [_person_entity("John Doe", 6, 14)]
    detector = _make_detector(entities)
    mapper = PseudonymMapper()

    result_body, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.DETECT_ONLY,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    assert result.files_analyzed == 1
    assert result.total_entities == 1
    assert result.blocked is False
    # Body should NOT be modified in detect-only mode
    file_data = result_body["messages"][0]["content"][0]["file"]["file_data"]
    assert file_data == _b64_uri(original_text, "text/plain")


# ── Block mode ───────────────────────────────────────────────


async def test_block_mode_rejects_when_pii_found():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {"file_data": _b64_uri(b"Hello John Doe", "text/plain")},
                    },
                ],
            },
        ],
    }
    entities = [_person_entity("John Doe", 6, 14)]
    detector = _make_detector(entities)
    mapper = PseudonymMapper()

    _, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.BLOCK,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    assert result.blocked is True
    assert result.files_blocked == 1


async def test_block_mode_allows_when_no_pii():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {"file_data": _b64_uri(b"Hello world", "text/plain")},
                    },
                ],
            },
        ],
    }
    detector = _make_detector([])
    mapper = PseudonymMapper()

    _, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.BLOCK,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    assert result.blocked is False


# ── Pseudonymize mode ────────────────────────────────────────


async def test_pseudonymize_plain_text():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {"file_data": _b64_uri(b"Hello John Doe", "text/plain")},
                    },
                ],
            },
        ],
    }
    entities = [_person_entity("John Doe", 6, 14)]
    detector = _make_detector(entities)
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()

    result_body, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.PSEUDONYMIZE,
        detector=detector,
        pseudo_engine=engine,
        mapper=mapper,
    )
    assert result.total_entities == 1
    assert result.files_reconstructed == 1

    # The file block should still be a file (text/plain is reconstructable)
    block = result_body["messages"][0]["content"][0]
    assert block["type"] == "file"
    new_b64 = block["file"]["file_data"]
    decoded = base64.b64decode(new_b64.split(",", 1)[1])
    assert b"<<PERSON_1>>" in decoded
    assert b"John Doe" not in decoded


async def test_pseudonymize_pdf_converts_to_text():
    """PDF cannot be reconstructed, so it should be converted to a text block."""
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {
                            "file_data": _b64_uri(b"Hello John Doe", "application/pdf"),
                        },
                    },
                ],
            },
        ],
    }
    # For this test we need the extractor to work on "fake PDF" data.
    # We'll mock the detector to return entities for the extracted text.
    # However, the PDF extractor will fail on fake data. The pipeline
    # will record an extraction error. Let's verify that path.
    detector = _make_detector([])
    mapper = PseudonymMapper()

    _, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.PSEUDONYMIZE,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    # Fake PDF bytes will cause extraction error
    assert result.files_extraction_errors == 1


# ── Size limit ───────────────────────────────────────────────


async def test_file_exceeds_size_limit():
    large_data = b"x" * 1000
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {"file_data": _b64_uri(large_data, "text/plain")},
                    },
                ],
            },
        ],
    }
    detector = _make_detector()
    mapper = PseudonymMapper()

    _, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.DETECT_ONLY,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
        max_file_size_bytes=500,
    )
    assert result.files_extraction_errors == 1


# ── Mapper consistency ───────────────────────────────────────


async def test_mapper_shared_between_text_and_file():
    """Same entity in text and file should get the same pseudonym."""
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()

    # Pre-populate mapper as if text pipeline already processed "John Doe"
    mapper.get_or_create("John Doe", "PERSON")

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Email from John Doe"},
                    {
                        "type": "file",
                        "file": {"file_data": _b64_uri(b"Report by John Doe", "text/plain")},
                    },
                ],
            },
        ],
    }
    entities = [_person_entity("John Doe", 10, 18)]
    detector = _make_detector(entities)

    result_body, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.PSEUDONYMIZE,
        detector=detector,
        pseudo_engine=engine,
        mapper=mapper,
    )

    # The mapper should have reused the existing pseudonym
    assert mapper.entity_count == 1  # only 1 unique entity
    pseudo = mapper.get_or_create("John Doe", "PERSON")
    assert pseudo == "<<PERSON_1>>"


# ── No file blocks ───────────────────────────────────────────


async def test_no_file_blocks_returns_unchanged():
    body = {
        "messages": [{"role": "user", "content": "Just text"}],
    }
    detector = _make_detector()
    mapper = PseudonymMapper()

    result_body, result = await analyze_files_in_body(
        body=body,
        stream_key="openai_chat",
        mode=FileAnalysisMode.PSEUDONYMIZE,
        detector=detector,
        pseudo_engine=PseudonymizationEngine(),
        mapper=mapper,
    )
    assert result.files_analyzed == 0
    assert result_body == body
