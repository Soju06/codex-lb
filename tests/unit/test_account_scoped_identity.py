"""Invariants for the per-account outbound thread identity mapping."""

from __future__ import annotations

import re
import uuid

import pytest

from app.core.clients.account_scoped_identity import (
    ACCOUNT_SCOPED_IDENTITY_NAMESPACE,
    NEVER_SCOPED_NAMES,
    SCOPED_OPAQUE_PREFIX,
    SCOPED_SESSION_HEADER_NAMES,
    scope_payload_thread_identity,
    scope_prompt_cache_key,
    scope_session_headers,
    scope_thread_value,
)

_INSTALL_A = "11111111-1111-4111-8111-111111111111"
_INSTALL_B = "22222222-2222-4222-8222-222222222222"
_UUID_SHAPE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def _scoped(value: str, installation_id: str) -> str:
    """``scope_thread_value`` with the ``None`` passthrough branch narrowed away."""
    result = scope_thread_value(value, installation_id)
    assert isinstance(result, str)
    return result


def test_same_pair_always_maps_to_the_same_value():
    value = "thread-abc"
    first = scope_thread_value(value, _INSTALL_A)
    for _ in range(5):
        assert scope_thread_value(value, _INSTALL_A) == first


def test_different_accounts_never_collide():
    value = "thread-abc"
    assert scope_thread_value(value, _INSTALL_A) != scope_thread_value(value, _INSTALL_B)


def test_length_framing_prevents_a_crafted_collision():
    # Without length framing "a" + "bc" and "ab" + "c" would hash identically.
    assert scope_thread_value("bc", "a") != scope_thread_value("c", "ab")


def test_uuid_shape_is_preserved():
    original = "0189b4d0-9e1d-7f2a-8c3b-1f2e3d4c5b6a"
    scoped = _scoped(original, _INSTALL_A)
    assert scoped != original
    assert _UUID_SHAPE.match(scoped)
    # A UUID-shaped output really is a UUID, not just a lookalike.
    assert str(uuid.UUID(scoped)) == scoped


def test_uppercase_uuid_is_still_treated_as_a_uuid():
    original = "0189B4D0-9E1D-7F2A-8C3B-1F2E3D4C5B6A"
    scoped = _scoped(original, _INSTALL_A)
    assert _UUID_SHAPE.match(scoped)


def test_non_uuid_value_gets_the_marked_opaque_form():
    scoped = _scoped("codex-session-42", _INSTALL_A)
    assert scoped.startswith(SCOPED_OPAQUE_PREFIX)
    assert len(scoped) == len(SCOPED_OPAQUE_PREFIX) + 32
    assert not _UUID_SHAPE.match(scoped)


def test_uuid_and_opaque_branches_agree_with_the_frozen_namespace():
    # The namespace is a wire constant: changing it re-keys every live thread,
    # so pin it rather than letting a refactor drift it silently.
    assert ACCOUNT_SCOPED_IDENTITY_NAMESPACE == uuid.UUID("6f3a4f52-0f59-5b3d-9d8f-0f2f5c9d4a71")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_values_pass_through(value):
    assert scope_thread_value(value, _INSTALL_A) == value


@pytest.mark.parametrize("salt", [None, ""])
def test_no_salt_is_an_identity_mapping(salt):
    assert scope_thread_value("thread-abc", salt) == "thread-abc"
    assert scope_prompt_cache_key("cache-abc", salt) == "cache-abc"


def test_turn_state_and_previous_response_id_are_outside_the_vocabulary():
    assert NEVER_SCOPED_NAMES == {"x-codex-turn-state", "previous_response_id"}
    assert not (SCOPED_SESSION_HEADER_NAMES & NEVER_SCOPED_NAMES)


def test_scope_session_headers_rewrites_only_the_vocabulary():
    inbound = {
        "Session_Id": "0189b4d0-9e1d-7f2a-8c3b-1f2e3d4c5b6a",
        "thread-id": "thread-7",
        "x-codex-conversation-id": "conv-7",
        "x-opencode-session": "oc-7",
        "x-codex-turn-state": "opaque-upstream-token",
        "User-Agent": "codex_cli_rs/0.150.1",
        "Authorization": "Bearer tok",
    }
    scoped = scope_session_headers(inbound, _INSTALL_A)

    # Names, casing and order survive.
    assert list(scoped) == list(inbound)
    # Continuity token and unrelated headers are byte-identical.
    assert scoped["x-codex-turn-state"] == "opaque-upstream-token"
    assert scoped["User-Agent"] == inbound["User-Agent"]
    assert scoped["Authorization"] == inbound["Authorization"]
    # Everything in the vocabulary moved, including the mixed-case name.
    for name in ("Session_Id", "thread-id", "x-codex-conversation-id", "x-opencode-session"):
        assert scoped[name] != inbound[name]
    assert _UUID_SHAPE.match(scoped["Session_Id"])


def test_scope_session_headers_without_a_salt_is_a_no_op():
    inbound = {"session_id": "s-1", "thread-id": "t-1"}
    assert scope_session_headers(inbound, None) == inbound


def test_scope_payload_leaves_previous_response_id_alone():
    payload = {
        "prompt_cache_key": "cache-1",
        "previous_response_id": "resp_abc123",
        "client_metadata": {
            "x-codex-parent-thread-id": "parent-1",
            "x-codex-turn-metadata": '{"installation_id":"x"}',
        },
    }
    assert scope_payload_thread_identity(payload, _INSTALL_A) is True
    assert payload["previous_response_id"] == "resp_abc123"
    assert payload["prompt_cache_key"] != "cache-1"
    assert payload["client_metadata"]["x-codex-parent-thread-id"] != "parent-1"
    assert payload["client_metadata"]["x-codex-turn-metadata"] == '{"installation_id":"x"}'


def test_scope_payload_does_not_mutate_a_shared_metadata_mapping():
    shared_metadata = {"x-codex-window-id": "win-1"}
    payload = {"prompt_cache_key": "cache-1", "client_metadata": shared_metadata}
    scope_payload_thread_identity(payload, _INSTALL_A)
    # ``to_payload()`` is a shallow dump; the model's own mapping must survive
    # untouched so a retry on another account starts from the original value.
    assert shared_metadata == {"x-codex-window-id": "win-1"}
    assert payload["client_metadata"]["x-codex-window-id"] != "win-1"


def test_scope_payload_reports_no_change_without_a_salt():
    payload = {"prompt_cache_key": "cache-1"}
    assert scope_payload_thread_identity(payload, None) is False
    assert payload == {"prompt_cache_key": "cache-1"}


def test_scope_payload_reports_no_change_when_there_is_nothing_to_scope():
    payload = {"model": "gpt-6", "previous_response_id": "resp_1"}
    assert scope_payload_thread_identity(payload, _INSTALL_A) is False
