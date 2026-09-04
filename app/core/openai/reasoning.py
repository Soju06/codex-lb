from __future__ import annotations


def resolve_wire_reasoning_effort(effort: str) -> str:
    """Translate Codex client-plane Ultra to the upstream reasoning effort.

    Ultra's delegation behavior belongs to the client. The model receives Max.
    Unknown efforts remain untouched for provider-specific compatibility.
    """
    return "max" if effort.strip().lower() == "ultra" else effort
