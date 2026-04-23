from __future__ import annotations

from noirdoc.pseudonymization.system_prompt import (
    PSEUDONYM_SYSTEM_INSTRUCTION,
    build_pseudonym_instruction,
    inject_pseudonym_context,
)

# ── Anthropic ────────────────────────────────────────────


def test_anthropic_no_system():
    body = {"messages": [{"role": "user", "content": "Hi"}]}
    result = inject_pseudonym_context(body, "anthropic")
    assert result["system"] == PSEUDONYM_SYSTEM_INSTRUCTION


def test_anthropic_string_system():
    body = {"system": "Be helpful.", "messages": []}
    result = inject_pseudonym_context(body, "anthropic")
    assert result["system"].startswith(PSEUDONYM_SYSTEM_INSTRUCTION)
    assert "Be helpful." in result["system"]


def test_anthropic_array_system():
    body = {
        "system": [{"type": "text", "text": "Be helpful."}],
        "messages": [],
    }
    result = inject_pseudonym_context(body, "anthropic")
    assert len(result["system"]) == 2
    assert result["system"][0]["text"] == PSEUDONYM_SYSTEM_INSTRUCTION
    assert result["system"][1]["text"] == "Be helpful."


# ── OpenAI Chat ──────────────────────────────────────────


def test_openai_chat_inject():
    body = {
        "messages": [
            {"role": "user", "content": "Hello <<PERSON_1>>"},
        ],
    }
    result = inject_pseudonym_context(body, "openai_chat")
    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "system"
    assert result["messages"][0]["content"] == PSEUDONYM_SYSTEM_INSTRUCTION
    assert result["messages"][1]["role"] == "user"


def test_openai_chat_existing_system():
    body = {
        "messages": [
            {"role": "system", "content": "You are a lawyer."},
            {"role": "user", "content": "Hello"},
        ],
    }
    result = inject_pseudonym_context(body, "openai_chat")
    assert len(result["messages"]) == 3
    assert result["messages"][0]["content"] == PSEUDONYM_SYSTEM_INSTRUCTION
    assert result["messages"][1]["content"] == "You are a lawyer."


# ── OpenAI Responses ─────────────────────────────────────


def test_openai_responses_no_instructions():
    body = {"input": "Hello <<PERSON_1>>"}
    result = inject_pseudonym_context(body, "openai_responses")
    assert result["instructions"] == PSEUDONYM_SYSTEM_INSTRUCTION


def test_openai_responses_existing_instructions():
    body = {"input": "Hello", "instructions": "Be concise."}
    result = inject_pseudonym_context(body, "openai_responses")
    assert result["instructions"].startswith(PSEUDONYM_SYSTEM_INSTRUCTION)
    assert "Be concise." in result["instructions"]


# ── Unknown provider (no-op) ─────────────────────────────


def test_unknown_provider_noop():
    body = {"messages": []}
    result = inject_pseudonym_context(body, "unknown_provider")
    assert result == body


# ── Custom label ────────────────────────────────────────


def test_build_instruction_default():
    instruction = build_pseudonym_instruction()
    assert instruction == PSEUDONYM_SYSTEM_INSTRUCTION
    assert "<<PERSON_1>>" in instruction


def test_build_instruction_custom_label():
    instruction = build_pseudonym_instruction("PLACEHOLDER")
    assert "<<PLACEHOLDER_1>>" in instruction
    assert "<<PLACEHOLDER_2>>" in instruction
    assert "PERSON" not in instruction


def test_inject_with_custom_label():
    body = {"messages": [{"role": "user", "content": "Hi"}]}
    result = inject_pseudonym_context(body, "anthropic", label="PLACEHOLDER")
    assert "<<PLACEHOLDER_1>>" in result["system"]
    assert "PERSON" not in result["system"]
