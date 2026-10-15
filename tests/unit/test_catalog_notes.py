from copy import deepcopy

import pytest

from app.modules.proxy.catalog_notes import with_astra_notes_default
from app.modules.proxy.schemas import CodexModelEntry, CodexTruncationPolicy


@pytest.mark.parametrize(
    "messages",
    [
        None,
        {},
        {"token_budget": {}},
        {"token_budget": {"enabled": False, "use_history_notes_extension": False}},
        {"token_budget": "invalid"},
    ],
)
def test_incomplete_notes_metadata_is_preserved(messages):
    entry = CodexModelEntry(
        slug="gpt-6-astra",
        display_name="Astra",
        description="",
        truncation_policy=CodexTruncationPolicy(mode="tokens", limit=10000),
        experimental_supported_tools=[],
        model_messages=messages,
        supports_experimental_context=False,
    )
    original = deepcopy(entry.model_dump())
    assert with_astra_notes_default(entry).model_dump() == original
