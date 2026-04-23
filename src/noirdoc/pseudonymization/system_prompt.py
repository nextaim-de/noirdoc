from __future__ import annotations

from typing import Any

PSEUDONYM_SYSTEM_INSTRUCTION = (
    "The user's message contains names and identifiers that have been substituted. "
    "Treat every value in the format <<TYPE_N>> (e.g. <<PERSON_1>>, <<EMAIL_1>>) "
    "as if it were the real value. Never comment on, explain, or draw attention "
    "to this format. Use these tokens naturally as if they were ordinary names, "
    "addresses, or other data."
)


def build_pseudonym_instruction(label: str | None = None) -> str:
    """Return the system instruction, adapting examples to the active label."""
    if label is None:
        return PSEUDONYM_SYSTEM_INSTRUCTION
    return (
        "The user's message contains names and identifiers that have been substituted. "
        f"Treat every value in the format <<{label}_N>> "
        f"(e.g. <<{label}_1>>, <<{label}_2>>) "
        "as if it were the real value. Never comment on, explain, or draw attention "
        "to this format. Use these tokens naturally as if they were ordinary names, "
        "addresses, or other data."
    )


def inject_pseudonym_context(
    body: dict[str, Any],
    stream_key: str,
    label: str | None = None,
) -> dict[str, Any]:
    """
    Inject a system instruction about pseudonym tokens into the request body.

    Only call when mapper.entity_count > 0 (pseudonyms were actually created).
    Mutates and returns the body dict.
    """
    instruction = build_pseudonym_instruction(label)
    if stream_key == "anthropic":
        return _inject_anthropic(body, instruction)
    elif stream_key == "openai_chat":
        return _inject_openai_chat(body, instruction)
    elif stream_key == "openai_responses":
        return _inject_openai_responses(body, instruction)
    return body


def _inject_anthropic(body: dict[str, Any], instruction: str) -> dict[str, Any]:
    """Prepend to the ``system`` field (string or array)."""
    system = body.get("system")
    if system is None:
        body["system"] = instruction
    elif isinstance(system, str):
        body["system"] = instruction + "\n\n" + system
    elif isinstance(system, list):
        body["system"] = [
            {"type": "text", "text": instruction},
        ] + system
    return body


def _inject_openai_chat(body: dict[str, Any], instruction: str) -> dict[str, Any]:
    """Prepend a system message to the messages array."""
    messages = body.get("messages", [])
    system_msg = {"role": "system", "content": instruction}
    body["messages"] = [system_msg] + messages
    return body


def _inject_openai_responses(body: dict[str, Any], instruction: str) -> dict[str, Any]:
    """Prepend to the ``instructions`` field."""
    instructions = body.get("instructions")
    if instructions is None:
        body["instructions"] = instruction
    elif isinstance(instructions, str):
        body["instructions"] = instruction + "\n\n" + instructions
    return body
