from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.core.exceptions import ProxyUpstreamError
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.context_codec import HistoryPartition, pack_history
from app.modules.proxy.request_policy import apply_api_key_enforcement

PARENT = "00000000-0000-4000-8000-000000000011"
FORK = "00000000-0000-4000-8000-000000000012"


@pytest.mark.parametrize(
    "case", ["fork", "other_key", "excluded_account", "invalid_source", "invalid_target", "unmarked"]
)
def test_result_replay_requires_canonical_sessions_key_and_account_scope(case):
    key = ApiKeyData(
        id="key",
        name="test",
        key_prefix="test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        last_used_at=None,
    )
    token = json.loads(
        pack_history(
            key.id,
            "invalid" if case == "invalid_source" else PARENT,
            [HistoryPartition(account_id="owner", result={"encrypted_output": "native-content"})],
        )
    )["encrypted_output"]
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "Test",
            "reasoning": {} if case == "unmarked" else {"context": "all_turns"},
            "client_metadata": {"session_id": "invalid" if case == "invalid_target" else FORK},
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [{"type": "encrypted_content", "encrypted_content": token}],
                }
            ],
        }
    )
    if case == "other_key":
        key = replace(key, id="foreign-key")
    elif case == "excluded_account":
        key = replace(key, account_assignment_scope_enabled=True, assigned_account_ids=[])
    if case == "fork":
        apply_api_key_enforcement(payload, key)
        assert payload.input == [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [{"type": "encrypted_content", "encrypted_content": "native-content"}],
            }
        ]
    else:
        with pytest.raises(ProxyUpstreamError) as exc:
            apply_api_key_enforcement(payload, key)
        assert exc.value.status_code == (400 if case == "invalid_source" else 403)
