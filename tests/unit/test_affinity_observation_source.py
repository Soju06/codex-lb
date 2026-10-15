"""One classification of `sticky_key_source`, shared by every request-log path.

`persist-affinity-decision-on-request-logs` landed the three `request_logs`
affinity columns but left `sticky_key_source` to four per-transport if/elif
ladders that had drifted apart, so the same continuity signal was labelled
differently depending on how the client connected. These tests pin the single
classification that replaced them, and the two cases the old ladders got wrong
for the derived-key cohort the column exists to measure.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.db.models import StickySessionKind
from app.modules.proxy._service.compact import _sticky_key_for_compact_request
from app.modules.proxy.affinity import _AffinityPolicy, _sticky_key_for_responses_request
from app.modules.proxy.affinity_observation import AFFINITY_SOURCES, AffinityObservation

pytestmark = pytest.mark.unit


def _payload(**updates: object) -> ResponsesRequest:
    body: dict[str, object] = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "stream": True,
        **updates,
    }
    return ResponsesRequest.model_validate(body)


def _payload_with_cache_key(cache_key: str) -> ResponsesRequest:
    payload = _payload()
    payload.prompt_cache_key = cache_key
    return payload


def _policy(headers: dict[str, str], **overrides: object) -> _AffinityPolicy:
    kwargs: dict[str, Any] = {
        "codex_session_affinity": True,
        "openai_cache_affinity": True,
        "openai_cache_affinity_max_age_seconds": 300,
        "sticky_threads_enabled": False,
    }
    payload = cast(ResponsesRequest, overrides.pop("payload", None)) or _payload()
    kwargs.update(overrides)
    return _sticky_key_for_responses_request(payload, headers, **kwargs)


def _source(policy: _AffinityPolicy, **kwargs: Any) -> str:
    return AffinityObservation.from_policy(policy, **kwargs).source


# --------------------------------------------------------------------------
# One source per signal
# --------------------------------------------------------------------------


def test_each_continuity_signal_has_its_own_source() -> None:
    assert _source(_policy({"session_id": "sid-1"})) == "session_header"
    assert _source(_policy({"thread-id": "thread-1"})) == "thread_header"
    assert _source(_policy({"x-codex-turn-state": "client-turn-owner"})) == "turn_state_header"
    assert _source(_policy({}, payload=_payload_with_cache_key("client-thread-1"))) == "payload"
    assert _source(_policy({})) == "derived"
    assert _source(_AffinityPolicy()) == "none"


def test_a_proxy_synthesized_turn_state_is_distinguished_from_a_client_one() -> None:
    synthesized = f"turn_{'a' * 32}"
    policy = _policy(
        {"x-codex-turn-state": synthesized},
        synthesized_turn_state=synthesized,
        openai_cache_affinity=False,
    )

    assert _source(policy) == "turn_state_header"
    assert _source(policy, synthesized_turn_state=synthesized) == "generated_turn_state"


def test_every_emitted_source_is_in_the_documented_domain() -> None:
    synthesized = f"turn_{'b' * 32}"
    emitted = {
        _source(policy)
        for policy in (
            _AffinityPolicy(),
            _policy({}),
            _policy({}, payload=_payload_with_cache_key("client-thread-1")),
            _policy({"session_id": "sid-1"}),
            _policy({"thread-id": "thread-1"}),
            _policy({"x-codex-turn-state": "client-turn-owner"}),
        )
    }
    emitted.add(
        _source(
            _policy(
                {"x-codex-turn-state": synthesized},
                synthesized_turn_state=synthesized,
                openai_cache_affinity=False,
            ),
            synthesized_turn_state=synthesized,
        )
    )

    assert emitted == set(AFFINITY_SOURCES)


# --------------------------------------------------------------------------
# The transports agree
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"session_id": "sid-1"}, "session_header"),
        # The HTTP stream ladder reported ``session_header`` here while the
        # compact, bridge and websocket ladders reported ``turn_state_header``.
        ({"session_id": "sid-1", "x-codex-turn-state": "client-turn-owner"}, "turn_state_header"),
        ({"thread-id": "thread-1"}, "thread_header"),
        ({}, "derived"),
    ],
)
def test_responses_and_compact_paths_agree_on_one_policy(headers: dict[str, str], expected: str) -> None:
    responses_policy = _policy(headers)
    compact_policy = _sticky_key_for_compact_request(
        # Same transcript as the responses fixture. An empty ``input`` is
        # unanchorable, so the derived-key arm would resolve to ``none`` on the
        # compact side only and the two transports would disagree for a reason
        # that has nothing to do with the ladder this test is about.
        ResponsesCompactRequest.model_validate(
            {
                "model": "gpt-5.1",
                "instructions": "hi",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            }
        ),
        headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert _source(responses_policy) == expected
    assert _source(compact_policy) == expected


def test_a_turn_state_request_is_never_labelled_session_header() -> None:
    policy = _policy({"session_id": "sid-1", "x-codex-turn-state": "client-turn-owner"})

    assert policy.codex_session_source == "turn_state"
    assert _source(policy) != "session_header"


# --------------------------------------------------------------------------
# payload vs derived
# --------------------------------------------------------------------------


def test_a_blank_client_cache_hint_is_recorded_as_derived() -> None:
    # The resolver strips the hint, rejects it as empty and derives its own
    # key; the row must describe the key that was actually produced.
    policy = _policy({}, payload=_payload_with_cache_key("   "))

    assert _source(policy) == "derived"


def test_reading_the_payload_after_resolution_cannot_change_the_source() -> None:
    # ``_resolve_prompt_cache_key`` writes the derived key back onto the
    # payload, so an emitter that recomputed "did the payload carry a cache
    # key" afterwards — as the bridge-to-stream fallback does — would report
    # ``payload`` for a key the proxy itself derived.
    payload = _payload()
    assert payload.prompt_cache_key is None

    policy = _policy({}, payload=payload)

    assert payload.prompt_cache_key is not None
    assert _source(policy) == "derived"


def test_the_resolver_records_the_cache_key_provenance_on_the_policy() -> None:
    assert _policy({}).prompt_cache_key_source == "derived"
    assert _policy({}, payload=_payload_with_cache_key("client-thread-1")).prompt_cache_key_source == "payload"


# --------------------------------------------------------------------------
# Kind, hash and retained source
# --------------------------------------------------------------------------


def test_kind_and_hash_describe_the_resolved_policy() -> None:
    policy = _policy({"session_id": "sid-1"})

    observation = AffinityObservation.from_policy(policy)

    assert observation.kind == StickySessionKind.CODEX_SESSION.value
    # The hash covers ``selection_key`` — the key selection routes on — not the
    # raw header, which for a soft session source is a different value.
    assert observation.key_hash is not None
    assert len(observation.key_hash) == 16
    assert all(character in "0123456789abcdef" for character in observation.key_hash)
    assert AffinityObservation.from_policy(_policy({"session_id": "sid-2"})).key_hash != observation.key_hash


def test_a_keyless_policy_reports_no_decision() -> None:
    observation = AffinityObservation.from_policy(_AffinityPolicy())

    assert (observation.source, observation.kind, observation.key_hash) == ("none", None, None)


def test_a_routing_adjustment_can_retain_the_originally_resolved_source() -> None:
    # Recovery clears the key so the replay is account-neutral, but the row
    # still reports which signal the original attempt resolved.
    resolved = _policy({"session_id": "sid-1"})
    observed = AffinityObservation.from_policy(resolved)
    neutralized = replace(resolved, key=None, kind=None, reallocate_sticky=True)

    retained = AffinityObservation.retaining_source(observed.source, neutralized)

    assert retained.source == "session_header"
    assert (retained.kind, retained.key_hash) == (None, None)
