"""Per-key activation defaults for the native Astra catalog entry."""

from pydantic import BaseModel, ConfigDict, ValidationError

from app.modules.proxy.schemas import CodexModelEntry


class _TokenBudgetDefaults(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    enabled: bool
    use_history_notes_extension: bool
    reminder_threshold_tokens: int
    reminder_message_template: str
    guidance_message: str
    auto_compact_fallback_prompt: str
    auto_compact_fallback_buffer_tokens: int


class _ModelMessages(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    token_budget: _TokenBudgetDefaults


def with_astra_notes_default(entry: CodexModelEntry) -> CodexModelEntry:
    if entry.slug != "gpt-6-astra" or entry.visibility != "list":
        return entry
    # Keep upstream-owned prompts intact. Incomplete metadata cannot safely
    # activate the extension, and modifying shared raw dictionaries leaks to keys.
    try:
        messages = _ModelMessages.model_validate((entry.model_extra or {}).get("model_messages"))
    except ValidationError:
        return entry
    messages.token_budget.enabled = True
    messages.token_budget.use_history_notes_extension = True
    return entry.model_copy(
        update={
            "supports_experimental_context": True,
            "model_messages": messages.model_dump(mode="json"),
        }
    )
