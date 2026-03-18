from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.service import (
    _derive_prompt_cache_key,
    _extract_first_user_input,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _make_api_key(id: str = "ak_test_001122334455") -> ApiKeyData:
    return ApiKeyData(
        id=id,
        name="test-key",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        expires_at=None,
        is_active=True,
        created_at=_NOW,
        last_used_at=None,
    )


class TestExtractFirstUserInput:
    def test_string_input(self):
        payload = ResponsesRequest(model="gpt-5.4", instructions="sys", input="hello world")
        assert _extract_first_user_input(payload) == "hello world"

    def test_string_input_truncated_to_512(self):
        long_text = "x" * 1000
        payload = ResponsesRequest(model="gpt-5.4", instructions="sys", input=long_text)
        assert _extract_first_user_input(payload) == long_text[:512]

    def test_user_message_with_text_content(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "response"},
                {"role": "user", "content": "second question"},
            ],
        )
        assert _extract_first_user_input(payload) == "first question"

    def test_user_message_with_structured_content(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "structured msg"}],
                },
            ],
        )
        assert _extract_first_user_input(payload) == "structured msg"

    def test_message_type_item_with_user_role(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "typed item"}],
                },
            ],
        )
        assert _extract_first_user_input(payload) == "typed item"

    def test_skips_assistant_and_system_items(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[
                {"role": "assistant", "content": "I am assistant"},
                {"type": "function_call", "name": "shell", "arguments": "{}"},
                {"role": "user", "content": "the real first input"},
            ],
        )
        assert _extract_first_user_input(payload) == "the real first input"

    def test_empty_input_array(self):
        payload = ResponsesRequest(model="gpt-5.4", instructions="sys", input=[])
        assert _extract_first_user_input(payload) is None

    def test_no_user_items(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[
                {"role": "assistant", "content": "only assistant"},
            ],
        )
        assert _extract_first_user_input(payload) is None

    def test_compact_request_string_input(self):
        payload = ResponsesCompactRequest(model="gpt-5.4", instructions="sys", input="compact hello")
        assert _extract_first_user_input(payload) == "compact hello"


class TestDerivePromptCacheKey:
    def test_same_session_across_turns_produces_same_key(self):
        turn1 = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are a helpful assistant",
            input=[{"role": "user", "content": "build a server"}],
        )
        turn2 = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are a helpful assistant",
            input=[
                {"role": "user", "content": "build a server"},
                {"role": "assistant", "content": "Sure, here is..."},
                {"role": "user", "content": "add logging"},
            ],
        )
        api_key = _make_api_key()
        key1 = _derive_prompt_cache_key(turn1, api_key)
        key2 = _derive_prompt_cache_key(turn2, api_key)
        assert key1 == key2

    def test_parallel_sessions_produce_different_keys(self):
        session_a = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are a helpful assistant",
            input=[{"role": "user", "content": "build a server"}],
        )
        session_b = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are a helpful assistant",
            input=[{"role": "user", "content": "write tests"}],
        )
        api_key = _make_api_key()
        key_a = _derive_prompt_cache_key(session_a, api_key)
        key_b = _derive_prompt_cache_key(session_b, api_key)
        assert key_a != key_b

    def test_different_api_keys_produce_different_keys(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="same instructions",
            input=[{"role": "user", "content": "same input"}],
        )
        key_a = _derive_prompt_cache_key(payload, _make_api_key(id="key_AAAAAA"))
        key_b = _derive_prompt_cache_key(payload, _make_api_key(id="key_BBBBBB"))
        assert key_a != key_b

    def test_different_instructions_produce_different_keys(self):
        api_key = _make_api_key()
        p1 = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are Codex",
            input=[{"role": "user", "content": "hello"}],
        )
        p2 = ResponsesRequest(
            model="gpt-5.4",
            instructions="You are a reviewer",
            input=[{"role": "user", "content": "hello"}],
        )
        assert _derive_prompt_cache_key(p1, api_key) != _derive_prompt_cache_key(p2, api_key)

    def test_no_api_key_still_produces_key(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[{"role": "user", "content": "hi"}],
        )
        key = _derive_prompt_cache_key(payload, None)
        assert isinstance(key, str)
        assert len(key) > 0

    def test_empty_instructions_and_empty_input(self):
        payload = ResponsesRequest(model="gpt-5.4", instructions="", input=[])
        key = _derive_prompt_cache_key(payload, None)
        assert isinstance(key, str)
        assert len(key) > 0

    def test_key_is_deterministic(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="sys",
            input=[{"role": "user", "content": "hello"}],
        )
        api_key = _make_api_key()
        keys = {_derive_prompt_cache_key(payload, api_key) for _ in range(10)}
        assert len(keys) == 1

    def test_compact_request_produces_key(self):
        payload = ResponsesCompactRequest(model="gpt-5.4", instructions="sys", input="compact input")
        key = _derive_prompt_cache_key(payload, _make_api_key())
        assert isinstance(key, str)
        assert len(key) > 0

    def test_key_parts_structure(self):
        payload = ResponsesRequest(
            model="gpt-5.4",
            instructions="instructions here",
            input=[{"role": "user", "content": "hello"}],
        )
        key = _derive_prompt_cache_key(payload, _make_api_key(id="ak_12345678ABCD"))
        parts = key.split("-")
        assert len(parts) == 3
        assert parts[0] == "ak_12345678ABCD"[:12]
        assert len(parts[1]) == 12
        assert len(parts[2]) == 12
