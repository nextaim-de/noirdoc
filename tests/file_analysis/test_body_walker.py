"""Tests for body_walker: file block extraction and result application."""

from __future__ import annotations

import base64

from noirdoc.file_analysis.body_walker import apply_file_results, extract_file_blocks
from noirdoc.file_analysis.models import FileAnalysisMode, FileBlock
from noirdoc.file_analysis.policy import FileAnalysisPolicy


def _b64_uri(content: bytes, mime: str = "text/plain") -> str:
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


# ── OpenAI Chat ──────────────────────────────────────────────


class TestExtractOpenAIChat:
    def test_image_url_block(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": _b64_uri(b"fake-png", "image/png")},
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_chat")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "image/png"
        assert blocks[0].content_bytes == b"fake-png"
        assert blocks[0].source_path == "messages[0].content[1]"
        assert blocks[0].source_type == "image_url"

    def test_file_block(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "file_data": _b64_uri(b"fake-pdf", "application/pdf"),
                                "filename": "doc.pdf",
                            },
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_chat")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "application/pdf"

    def test_file_id_reference_skipped(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "file", "file": {"file_id": "file-abc123"}},
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_chat")
        assert len(blocks) == 0

    def test_external_url_skipped(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/img.png"},
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_chat")
        assert len(blocks) == 0

    def test_text_only_no_blocks(self):
        body = {
            "messages": [
                {"role": "user", "content": "Hello"},
            ],
        }
        blocks = extract_file_blocks(body, "openai_chat")
        assert len(blocks) == 0


# ── OpenAI Responses API ────────────────────────────────────


class TestExtractOpenAIResponses:
    def test_input_image(self):
        body = {
            "input": [
                {
                    "type": "input_image",
                    "image_url": _b64_uri(b"fake-img", "image/jpeg"),
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_responses")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "image/jpeg"
        assert blocks[0].source_type == "input_image"

    def test_input_file(self):
        body = {
            "input": [
                {
                    "type": "input_file",
                    "file_data": _b64_uri(b"fake-doc", "application/pdf"),
                    "filename": "report.pdf",
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_responses")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "application/pdf"

    def test_nested_content(self):
        body = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": _b64_uri(b"nested-img", "image/png"),
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "openai_responses")
        assert len(blocks) == 1
        assert blocks[0].source_path == "input[0].content[0]"


# ── Anthropic ────────────────────────────────────────────────


class TestExtractAnthropic:
    def test_image_block(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(b"fake-png").decode(),
                            },
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "anthropic")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "image/png"
        assert blocks[0].content_bytes == b"fake-png"

    def test_document_block(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(b"fake-pdf").decode(),
                            },
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "anthropic")
        assert len(blocks) == 1
        assert blocks[0].mime_type == "application/pdf"

    def test_url_source_skipped(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/img.png",
                            },
                        },
                    ],
                },
            ],
        }
        blocks = extract_file_blocks(body, "anthropic")
        assert len(blocks) == 0

    def test_system_blocks(self):
        body = {
            "messages": [],
            "system": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(b"sys-pdf").decode(),
                    },
                },
            ],
        }
        blocks = extract_file_blocks(body, "anthropic")
        assert len(blocks) == 1
        assert blocks[0].source_path == "system[0]"


# ── Apply Results ────────────────────────────────────────────


class TestApplyFileResults:
    def test_passthrough_no_changes(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        policy = FileAnalysisPolicy(FileAnalysisMode.PASSTHROUGH)
        result = apply_file_results(body, "openai_chat", [], policy)
        assert result == body

    def test_detect_only_no_changes(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        policy = FileAnalysisPolicy(FileAnalysisMode.DETECT_ONLY)
        result = apply_file_results(body, "openai_chat", [], policy)
        assert result == body

    def test_pseudonymize_converts_pdf_to_text_openai_chat(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this"},
                        {
                            "type": "file",
                            "file": {
                                "file_data": _b64_uri(b"fake", "application/pdf"),
                            },
                        },
                    ],
                },
            ],
        }
        block = FileBlock(
            content_bytes=b"fake",
            mime_type="application/pdf",
            source_path="messages[0].content[1]",
            source_type="file",
            pseudonymized_text="<<PERSON_1>> sent an invoice.",
        )
        policy = FileAnalysisPolicy(FileAnalysisMode.PSEUDONYMIZE)
        result = apply_file_results(body, "openai_chat", [block], policy)
        replaced = result["messages"][0]["content"][1]
        assert replaced["type"] == "text"
        assert replaced["text"] == "<<PERSON_1>> sent an invoice."

    def test_pseudonymize_converts_to_text_anthropic(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(b"fake").decode(),
                            },
                        },
                    ],
                },
            ],
        }
        block = FileBlock(
            content_bytes=b"fake",
            mime_type="application/pdf",
            source_path="messages[0].content[0]",
            source_type="document",
            pseudonymized_text="<<PERSON_1>> report",
        )
        policy = FileAnalysisPolicy(FileAnalysisMode.PSEUDONYMIZE)
        result = apply_file_results(body, "anthropic", [block], policy)
        replaced = result["messages"][0]["content"][0]
        assert replaced["type"] == "text"
        assert replaced["text"] == "<<PERSON_1>> report"

    def test_pseudonymize_plain_text_reconstructs(self):
        original_text = b"Hello John Doe"
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "file_data": _b64_uri(original_text, "text/plain"),
                            },
                        },
                    ],
                },
            ],
        }
        block = FileBlock(
            content_bytes=original_text,
            mime_type="text/plain",
            source_path="messages[0].content[0]",
            source_type="file",
            pseudonymized_text="Hello <<PERSON_1>>",
        )
        policy = FileAnalysisPolicy(FileAnalysisMode.PSEUDONYMIZE)
        result = apply_file_results(body, "openai_chat", [block], policy)
        replaced = result["messages"][0]["content"][0]
        # For text/plain, the file block should be reconstructed (new base64)
        assert replaced["type"] == "file"
        new_data = replaced["file"]["file_data"]
        assert new_data.startswith("data:text/plain;base64,")
        decoded = base64.b64decode(new_data.split(",", 1)[1])
        assert decoded == b"Hello <<PERSON_1>>"
