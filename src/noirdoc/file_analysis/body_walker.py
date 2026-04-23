"""Walk provider-specific request bodies to extract and replace file blocks.

Each provider (OpenAI Chat, OpenAI Responses, Anthropic) uses a different
JSON structure for inline file content.  This module knows how to:

1. **Extract** :class:`FileBlock` objects from a parsed request body.
2. **Apply** analysis results back to the body (e.g. replacing a file block
   with a text block when pseudonymising a PDF).
"""

from __future__ import annotations

import base64
import copy
import re
from typing import Any

from noirdoc.file_analysis.mime import decode_base64_data_uri
from noirdoc.file_analysis.models import FileBlock
from noirdoc.file_analysis.policy import FileAnalysisPolicy
from noirdoc.file_analysis.reconstruction import can_reconstruct, reconstruct

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_file_blocks(body: dict[str, Any], stream_key: str) -> list[FileBlock]:
    """Walk *body* and return a :class:`FileBlock` for every inline file."""
    if stream_key == "openai_chat":
        return _extract_openai_chat(body)
    if stream_key == "openai_responses":
        return _extract_openai_responses(body)
    if stream_key == "anthropic":
        return _extract_anthropic(body)
    return []


def convert_blocks_to_text(
    body: dict[str, Any],
    stream_key: str,
    blocks: list[FileBlock],
) -> dict[str, Any]:
    """Replace *blocks* with text content blocks containing their extracted text.

    Unlike :func:`apply_file_results` (which only acts in pseudonymize mode),
    this function unconditionally converts the given blocks to text — used to
    convert provider-unsupported file types (XLSX, DOCX) before forwarding.
    """
    if not blocks:
        return body

    body = copy.deepcopy(body)

    for block in blocks:
        if not block.extracted_text:
            continue

        if stream_key == "openai_chat":
            parts = _parse_path(block.source_path)
            if parts is None:
                continue
            msg_idx, part_idx = parts
            messages = body.get("messages", [])
            if msg_idx >= len(messages):
                continue
            content = messages[msg_idx].get("content")
            if not isinstance(content, list) or part_idx >= len(content):
                continue
            content[part_idx] = {"type": "text", "text": block.extracted_text}

        elif stream_key == "openai_responses":
            ref = _navigate_to_ref(body, block.source_path)
            if ref is None:
                continue
            container, idx = ref
            container[idx] = {"type": "input_text", "text": block.extracted_text}

        elif stream_key == "anthropic":
            ref = _navigate_to_ref_anthropic(body, block)
            if ref is None:
                continue
            container, idx = ref
            container[idx] = {"type": "text", "text": block.extracted_text}

    return body


def apply_file_results(
    body: dict[str, Any],
    stream_key: str,
    blocks: list[FileBlock],
    policy: FileAnalysisPolicy,
) -> dict[str, Any]:
    """Mutate *body* in-place according to the analysis results.

    For **pseudonymize** mode:
    * Reconstructable formats (DOCX / XLSX / plain text) are reconstructed
      with pseudonymised content and the base64 payload is replaced.
    * Non-reconstructable formats (PDF / images) have their file block
      converted to a text block containing the pseudonymised extracted text.

    Other modes leave the body unchanged (PII is only logged / blocked).
    """
    if not policy.should_pseudonymize():
        return body

    body = copy.deepcopy(body)

    if stream_key == "openai_chat":
        _apply_openai_chat(body, blocks)
    elif stream_key == "openai_responses":
        _apply_openai_responses(body, blocks)
    elif stream_key == "anthropic":
        _apply_anthropic(body, blocks)

    return body


# ---------------------------------------------------------------------------
# OpenAI Chat Completions
# ---------------------------------------------------------------------------

_OPENAI_CHAT_FILE_TYPES = {"image_url", "file"}


def _extract_openai_chat(body: dict[str, Any]) -> list[FileBlock]:
    blocks: list[FileBlock] = []
    for msg_idx, msg in enumerate(body.get("messages", [])):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part_idx, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            block_type = part.get("type", "")
            if block_type not in _OPENAI_CHAT_FILE_TYPES:
                continue
            fb = _decode_openai_chat_block(part, block_type, msg_idx, part_idx)
            if fb:
                blocks.append(fb)
    return blocks


def _decode_openai_chat_block(
    part: dict,
    block_type: str,
    msg_idx: int,
    part_idx: int,
) -> FileBlock | None:
    path = f"messages[{msg_idx}].content[{part_idx}]"

    if block_type == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        if not url.startswith("data:"):
            return None  # external URL – cannot analyse inline
        try:
            raw, mime = decode_base64_data_uri(url)
        except ValueError:
            return None
        return FileBlock(
            content_bytes=raw,
            mime_type=mime,
            source_path=path,
            source_type=block_type,
        )

    if block_type == "file":
        file_obj = part.get("file") or {}
        file_data = file_obj.get("file_data", "")
        if not file_data.startswith("data:"):
            return None  # file_id reference – not inline
        try:
            raw, mime = decode_base64_data_uri(file_data)
        except ValueError:
            return None
        return FileBlock(
            content_bytes=raw,
            mime_type=mime,
            source_path=path,
            source_type=block_type,
        )

    return None


def _apply_openai_chat(body: dict[str, Any], blocks: list[FileBlock]) -> None:
    for block in blocks:
        if block.pseudonymized_text is None:
            continue
        parts = _parse_path(block.source_path)
        if parts is None:
            continue
        msg_idx, part_idx = parts
        messages = body.get("messages", [])
        if msg_idx >= len(messages):
            continue
        content = messages[msg_idx].get("content")
        if not isinstance(content, list) or part_idx >= len(content):
            continue

        if can_reconstruct(block.mime_type):
            _replace_base64_openai_chat(content[part_idx], block)
        else:
            content[part_idx] = {"type": "text", "text": block.pseudonymized_text}


def _replace_base64_openai_chat(part: dict, block: FileBlock) -> None:
    """Replace base64 payload inside an OpenAI Chat file/image_url block."""
    new_bytes = reconstruct(block)
    if new_bytes is None:
        part.clear()
        part.update({"type": "text", "text": block.pseudonymized_text or ""})
        return

    b64 = base64.b64encode(new_bytes).decode()
    data_uri = f"data:{block.mime_type};base64,{b64}"

    if block.source_type == "image_url":
        part.setdefault("image_url", {})["url"] = data_uri
    elif block.source_type == "file":
        part.setdefault("file", {})["file_data"] = data_uri


# ---------------------------------------------------------------------------
# OpenAI Responses API
# ---------------------------------------------------------------------------

_OPENAI_RESPONSES_FILE_TYPES = {"input_image", "input_file"}


def _extract_openai_responses(body: dict[str, Any]) -> list[FileBlock]:
    blocks: list[FileBlock] = []
    inp = body.get("input")
    if not isinstance(inp, list):
        return blocks
    for item_idx, item in enumerate(inp):
        if not isinstance(item, dict):
            continue
        # Top-level typed blocks
        block_type = item.get("type", "")
        if block_type in _OPENAI_RESPONSES_FILE_TYPES:
            fb = _decode_responses_block(item, block_type, f"input[{item_idx}]")
            if fb:
                blocks.append(fb)
        # Nested content arrays
        content = item.get("content")
        if isinstance(content, list):
            for part_idx, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                bt = part.get("type", "")
                if bt in _OPENAI_RESPONSES_FILE_TYPES:
                    fb = _decode_responses_block(part, bt, f"input[{item_idx}].content[{part_idx}]")
                    if fb:
                        blocks.append(fb)
    return blocks


def _decode_responses_block(item: dict, block_type: str, path: str) -> FileBlock | None:
    if block_type == "input_image":
        url = item.get("image_url", "")
        if not url.startswith("data:"):
            return None
        try:
            raw, mime = decode_base64_data_uri(url)
        except ValueError:
            return None
        return FileBlock(
            content_bytes=raw,
            mime_type=mime,
            source_path=path,
            source_type=block_type,
        )

    if block_type == "input_file":
        file_data = item.get("file_data", "")
        if not file_data.startswith("data:"):
            return None
        try:
            raw, mime = decode_base64_data_uri(file_data)
        except ValueError:
            return None
        return FileBlock(
            content_bytes=raw,
            mime_type=mime,
            source_path=path,
            source_type=block_type,
        )

    return None


def _apply_openai_responses(body: dict[str, Any], blocks: list[FileBlock]) -> None:
    for block in blocks:
        if block.pseudonymized_text is None:
            continue
        _apply_at_path_responses(body, block)


def _apply_at_path_responses(body: dict[str, Any], block: FileBlock) -> None:
    """Navigate to the block's source path in the Responses input and replace."""
    ref = _navigate_to_ref(body, block.source_path)
    if ref is None:
        return

    container, idx = ref
    if can_reconstruct(block.mime_type):
        new_bytes = reconstruct(block)
        if new_bytes is not None:
            b64 = base64.b64encode(new_bytes).decode()
            data_uri = f"data:{block.mime_type};base64,{b64}"
            if block.source_type == "input_image":
                container[idx]["image_url"] = data_uri
            elif block.source_type == "input_file":
                container[idx]["file_data"] = data_uri
            return

    # Fallback: convert to text
    container[idx] = {"type": "input_text", "text": block.pseudonymized_text}


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------

_ANTHROPIC_FILE_TYPES = {"image", "document"}


def _extract_anthropic(body: dict[str, Any]) -> list[FileBlock]:
    blocks: list[FileBlock] = []
    for msg_idx, msg in enumerate(body.get("messages", [])):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for blk_idx, blk in enumerate(content):
            if not isinstance(blk, dict):
                continue
            bt = blk.get("type", "")
            if bt in _ANTHROPIC_FILE_TYPES:
                fb = _decode_anthropic_block(blk, bt, f"messages[{msg_idx}].content[{blk_idx}]")
                if fb:
                    blocks.append(fb)

    # System can also be an array
    system = body.get("system")
    if isinstance(system, list):
        for blk_idx, blk in enumerate(system):
            if not isinstance(blk, dict):
                continue
            bt = blk.get("type", "")
            if bt in _ANTHROPIC_FILE_TYPES:
                fb = _decode_anthropic_block(blk, bt, f"system[{blk_idx}]")
                if fb:
                    blocks.append(fb)
    return blocks


def _decode_anthropic_block(blk: dict, block_type: str, path: str) -> FileBlock | None:
    source = blk.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") != "base64":
        return None  # URL source – not inline

    media_type = source.get("media_type", "")
    data_b64 = source.get("data", "")
    if not data_b64:
        return None

    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return None

    return FileBlock(
        content_bytes=raw,
        mime_type=media_type,
        source_path=path,
        source_type=block_type,
    )


def _apply_anthropic(body: dict[str, Any], blocks: list[FileBlock]) -> None:
    for block in blocks:
        if block.pseudonymized_text is None:
            continue
        ref = _navigate_to_ref_anthropic(body, block)
        if ref is None:
            continue

        container, idx = ref
        if can_reconstruct(block.mime_type):
            new_bytes = reconstruct(block)
            if new_bytes is not None:
                b64 = base64.b64encode(new_bytes).decode()
                container[idx]["source"]["data"] = b64
                continue

        # Fallback: convert to text
        container[idx] = {"type": "text", "text": block.pseudonymized_text}


# ---------------------------------------------------------------------------
# Path navigation helpers
# ---------------------------------------------------------------------------

_PATH_PART_RE = re.compile(r"([a-z_]+)\[(\d+)\]")


def _parse_path(path: str) -> tuple[int, int] | None:
    """Parse ``messages[0].content[1]`` into ``(0, 1)``."""
    parts = path.split(".")
    if len(parts) != 2:
        return None
    m1 = _PATH_PART_RE.fullmatch(parts[0])
    m2 = _PATH_PART_RE.fullmatch(parts[1])
    if not m1 or not m2:
        return None
    return int(m1.group(2)), int(m2.group(2))


def _navigate_to_ref(body: dict[str, Any], path: str) -> tuple[list, int] | None:
    """Navigate a JSON path like ``input[0].content[1]`` and return (list, index)."""
    parts = path.split(".")
    current: Any = body
    for i, part in enumerate(parts):
        m = _PATH_PART_RE.fullmatch(part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            arr = current.get(key) if isinstance(current, dict) else None
            if not isinstance(arr, list) or idx >= len(arr):
                return None
            if i == len(parts) - 1:
                return arr, idx
            current = arr[idx]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
    return None


def _navigate_to_ref_anthropic(body: dict[str, Any], block: FileBlock) -> tuple[list, int] | None:
    """Navigate using source_path for Anthropic format."""
    path = block.source_path
    if path.startswith("system["):
        m = re.fullmatch(r"system\[(\d+)\]", path)
        if not m:
            return None
        idx = int(m.group(1))
        system = body.get("system")
        if not isinstance(system, list) or idx >= len(system):
            return None
        return system, idx

    return _navigate_to_ref(body, path)
