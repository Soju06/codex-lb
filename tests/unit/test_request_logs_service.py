from __future__ import annotations

from datetime import datetime

import pytest

from app.modules.request_logs.repository import (
    ConversationFacet,
    ConversationListResult,
    ConversationListSummary,
)
from app.modules.request_logs.service import _to_conversations


@pytest.mark.parametrize(
    ("api_key_names", "expected_id", "expected_name"),
    [({}, None, None), ({"key-selected": "Selected key"}, "key-selected", "Selected key")],
)
def test_to_conversations_only_emits_api_key_fields_for_resolved_safe_name(
    api_key_names: dict[str, str], expected_id: str | None, expected_name: str | None
) -> None:
    result = ConversationListResult(
        summaries=[
            ConversationListSummary(
                conversation_id="conversation-1",
                last_requested_at=datetime(2026, 7, 24),
                account_count=0,
                total_tokens=0,
                cached_input_tokens=0,
                cost_usd=0.0,
            )
        ],
        account_facets=[],
        api_key_facets=[ConversationFacet("conversation-1", "key-selected", 1)],
        model_facets=[],
        total=1,
    )

    [entry] = _to_conversations(result, api_key_names)

    assert entry.api_key_id == expected_id
    assert entry.api_key_name == expected_name
