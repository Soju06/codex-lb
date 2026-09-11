"""Behaviour of the thread-anchored ``prompt_cache_key`` derivation.

These tests assert what the key must *do* for an unanchored HTTP thread --
stay put while the transcript grows, stay put when the client trims its
leading history, never merge two distinct threads, and say so honestly when
there is nothing to anchor -- rather than the literal shape of the key.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import cast

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.affinity import (
    DERIVATION_OUTCOME_ANCHOR_HIT,
    DERIVATION_OUTCOME_ANCHOR_NEW,
    DERIVATION_OUTCOME_ANCHOR_RESET,
    DERIVATION_OUTCOME_PAYLOAD,
    DERIVATION_OUTCOME_UNANCHORABLE,
    _derive_prompt_cache_anchor,
    _derive_prompt_cache_key,
    _resolve_prompt_cache_key,
    _sticky_key_for_responses_request,
)
from app.modules.proxy.thread_anchors import (
    _MAX_ITEM_ENCODED_CHARS,
    ThreadAnchorIndex,
    build_thread_window,
    get_thread_anchor_index,
    reset_thread_anchor_index,
    thread_anchor_domain,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_anchor_index():
    reset_thread_anchor_index()
    yield
    reset_thread_anchor_index()


def _json_value(value: object) -> JsonValue:
    return cast(JsonValue, value)


def _make_api_key(id: str = "ak_test_001122334455") -> ApiKeyData:
    return ApiKeyData(
        id=id,
        name="test-key",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=_NOW,
        last_used_at=None,
    )


def _request(items: list[object], *, model: str = "gpt-5.4", instructions: str = "You are Codex") -> ResponsesRequest:
    return ResponsesRequest(model=model, instructions=instructions, input=_json_value(items))


def _env_item(cwd: str = "/repo") -> dict[str, object]:
    return {"role": "user", "content": [{"type": "input_text", "text": f"<environment_context>cwd={cwd}"}]}


def _user(text: str) -> dict[str, object]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant(text: str) -> dict[str, object]:
    return {"role": "assistant", "content": [{"type": "output_text", "text": text}]}


class TestAppendStability:
    def test_key_is_stable_across_a_growing_transcript(self):
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("build a server")]
        first = _derive_prompt_cache_key(_request(items), api_key)
        for turn in range(1, 30):
            items = [*items, _assistant(f"reply {turn}"), _user(f"follow-up {turn}")]
            anchor = _derive_prompt_cache_anchor(_request(items), api_key)
            assert anchor.sticky_key == first, f"key moved on turn {turn}"
            assert anchor.outcome == DERIVATION_OUTCOME_ANCHOR_HIT

    def test_transcript_longer_than_the_retained_window_stays_stable(self):
        """The window slides; the anchor does not."""
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("start")]
        first = _derive_prompt_cache_key(_request(items), api_key)
        for turn in range(1, 200):
            items = [*items, _assistant(f"a{turn}"), _user(f"u{turn}")]
            assert _derive_prompt_cache_key(_request(items), api_key) == first

    def test_identical_re_derivation_returns_the_same_key(self):
        """A second derivation of the same body must not mint a second key."""
        api_key = _make_api_key()
        payload = _request([_env_item(), _user("hello"), _assistant("hi"), _user("again")])
        keys = {_derive_prompt_cache_key(payload, api_key) for _ in range(10)}
        assert len(keys) == 1

    def test_volatile_trailing_content_does_not_move_the_key(self):
        """Only the newest item changes between turns; older items anchor it."""
        api_key = _make_api_key()
        base: list[object] = [_env_item(), _user("start"), _assistant("ok")]
        first = _derive_prompt_cache_key(_request([*base, _user("t=1")]), api_key)
        second = _derive_prompt_cache_key(_request([*base, _user("t=1"), _assistant("ok"), _user("t=2")]), api_key)
        assert second == first


class TestLeadingTrimStability:
    def test_key_survives_the_client_trimming_leading_history(self):
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("u0")]
        for turn in range(1, 12):
            items = [*items, _assistant(f"a{turn}"), _user(f"u{turn}")]
        first = _derive_prompt_cache_key(_request(items), api_key)

        # Client drops the four oldest items and appends the next turn.
        trimmed = [*items[4:], _assistant("a12"), _user("u12")]
        anchor = _derive_prompt_cache_anchor(_request(trimmed), api_key)
        assert anchor.sticky_key == first
        assert anchor.outcome == DERIVATION_OUTCOME_ANCHOR_HIT

        # And the trimmed shape keeps anchoring on the next turn too.
        again = [*trimmed[2:], _assistant("a13"), _user("u13")]
        assert _derive_prompt_cache_key(_request(again), api_key) == first

    def test_compaction_mints_a_new_anchor_and_reports_the_reset(self):
        """A summarised turn does not extend the transcript; the cache is cold."""
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("u0")]
        for turn in range(1, 8):
            items = [*items, _assistant(f"a{turn}"), _user(f"u{turn}")]
        first = _derive_prompt_cache_key(_request(items), api_key)

        compacted = [_env_item(), _user("u0"), _assistant("<summary of 7 turns>"), _user("u8")]
        anchor = _derive_prompt_cache_anchor(_request(compacted), api_key)
        assert anchor.sticky_key != first
        assert anchor.outcome == DERIVATION_OUTCOME_ANCHOR_RESET


class TestDistinctThreadsStayDistinct:
    def test_two_threads_from_one_api_key_sharing_their_opening_item_stay_separate(self):
        api_key = _make_api_key()
        thread_a = [_env_item(), _user("refactor the parser")]
        thread_b = [_env_item(), _user("write the release notes")]
        key_a = _derive_prompt_cache_key(_request(thread_a), api_key)
        key_b = _derive_prompt_cache_key(_request(thread_b), api_key)
        assert key_a != key_b

        # They must stay separate as both grow.
        for turn in range(1, 10):
            thread_a = [*thread_a, _assistant(f"a{turn}"), _user(f"a-follow {turn}")]
            thread_b = [*thread_b, _assistant(f"b{turn}"), _user(f"b-follow {turn}")]
            assert _derive_prompt_cache_key(_request(thread_a), api_key) == key_a
            assert _derive_prompt_cache_key(_request(thread_b), api_key) == key_b

    def test_instructions_that_differ_only_past_512_characters_do_not_merge(self):
        """The legacy derivation hashed ``instructions[:512]`` and collided here."""
        api_key = _make_api_key()
        shared = "S" * 600
        items = [_env_item(), _user("same opening")]
        key_a = _derive_prompt_cache_key(_request(items, instructions=shared + "-alpha"), api_key)
        key_b = _derive_prompt_cache_key(_request(items, instructions=shared + "-beta"), api_key)
        assert key_a != key_b

    def test_first_items_sharing_512_characters_do_not_merge(self):
        api_key = _make_api_key()
        shared = "X" * 800
        key_a = _derive_prompt_cache_key(_request([_user(shared + "alpha"), _user("a")]), api_key)
        key_b = _derive_prompt_cache_key(_request([_user(shared + "beta"), _user("b")]), api_key)
        assert key_a != key_b

    def test_different_api_keys_never_share_an_anchor(self):
        items = [_env_item(), _user("identical body")]
        key_a = _derive_prompt_cache_key(_request(items), _make_api_key(id="key_AAAAAAAAAAAA"))
        key_b = _derive_prompt_cache_key(_request(items), _make_api_key(id="key_BBBBBBBBBBBB"))
        assert key_a != key_b

    def test_different_model_classes_never_share_an_anchor(self):
        api_key = _make_api_key()
        items = [_env_item(), _user("identical body")]
        mini = _derive_prompt_cache_key(_request(items, model="gpt-5.4-mini"), api_key)
        codex = _derive_prompt_cache_key(_request(items, model="gpt-5.3-codex"), api_key)
        std = _derive_prompt_cache_key(_request(items, model="gpt-5.4"), api_key)
        assert len({mini, codex, std}) == 3
        assert "-mini-" in mini
        assert "-codex-" in codex
        assert "-std-" in std

    def test_random_independent_sequences_never_merge(self):
        """Property-style: appends never move a key, and threads never cross."""
        api_key = _make_api_key()
        rng = random.Random(20260911)
        threads: list[list[object]] = [[_env_item(), _user(f"seed-{index}")] for index in range(12)]
        keys = [_derive_prompt_cache_key(_request(items), api_key) for items in threads]
        assert len(set(keys)) == len(keys)
        for _ in range(200):
            choice = rng.randrange(len(threads))
            threads[choice] = [
                *threads[choice],
                _assistant(f"reply-{rng.random()}"),
                _user(f"ask-{rng.random()}"),
            ]
            assert _derive_prompt_cache_key(_request(threads[choice]), api_key) == keys[choice]
        assert len(set(keys)) == len(keys)


class TestUnanchorableRequests:
    def test_empty_input_is_reported_unanchorable_instead_of_randomly_keyed(self):
        payload = _request([], instructions="")
        first = _derive_prompt_cache_anchor(payload, None)
        second = _derive_prompt_cache_anchor(_request([], instructions=""), None)
        assert first.outcome == DERIVATION_OUTCOME_UNANCHORABLE
        assert first.sticky_key is None
        # The forwarded key is still attached and is stable, not a fresh uuid.
        assert first.attached_key == second.attached_key
        assert first.attached_key

    def test_single_item_body_is_unanchorable(self):
        """One item is no transcript: minting would write a single-use row."""
        anchor = _derive_prompt_cache_anchor(_request([_user("hi")]), _make_api_key())
        assert anchor.outcome == DERIVATION_OUTCOME_UNANCHORABLE
        assert anchor.sticky_key is None

    def test_string_input_is_unanchorable(self):
        payload = ResponsesRequest(model="gpt-5.4", instructions="sys", input="hello world")
        anchor = _derive_prompt_cache_anchor(payload, _make_api_key())
        assert anchor.outcome == DERIVATION_OUTCOME_UNANCHORABLE
        assert anchor.sticky_key is None

    def test_unanchorable_request_writes_no_sticky_key(self):
        payload = _request([], instructions="")
        policy = _sticky_key_for_responses_request(
            payload,
            {},
            codex_session_affinity=True,
            openai_cache_affinity=True,
            openai_cache_affinity_max_age_seconds=1800,
            sticky_threads_enabled=True,
            api_key=_make_api_key(),
        )
        assert policy.key is None
        assert policy.prompt_cache_derivation_outcome == DERIVATION_OUTCOME_UNANCHORABLE
        # The spec still requires a stable derived key on the forwarded payload.
        assert isinstance(payload.prompt_cache_key, str) and payload.prompt_cache_key

    def test_anchored_request_still_carries_a_sticky_key(self):
        payload = _request([_env_item(), _user("do the thing")])
        policy = _sticky_key_for_responses_request(
            payload,
            {},
            codex_session_affinity=True,
            openai_cache_affinity=True,
            openai_cache_affinity_max_age_seconds=1800,
            sticky_threads_enabled=True,
            api_key=_make_api_key(),
        )
        assert policy.key is not None
        assert policy.key == payload.prompt_cache_key
        assert policy.kind == StickySessionKind.PROMPT_CACHE
        assert policy.prompt_cache_derivation_outcome == DERIVATION_OUTCOME_ANCHOR_NEW


class TestResolution:
    def test_client_supplied_key_is_forwarded_unchanged(self):
        payload = _request([_env_item(), _user("hello")])
        payload.prompt_cache_key = "client-key"
        resolution = _resolve_prompt_cache_key(payload, openai_cache_affinity=True, api_key=_make_api_key())
        assert resolution.sticky_key == "client-key"
        assert resolution.outcome == DERIVATION_OUTCOME_PAYLOAD
        assert payload.prompt_cache_key == "client-key"

    def test_compact_request_is_anchored_like_a_responses_request(self):
        api_key = _make_api_key()
        payload = ResponsesCompactRequest(
            model="gpt-5.4",
            instructions="sys",
            input=_json_value([_env_item(), _user("compact me")]),
        )
        anchor = _derive_prompt_cache_anchor(payload, api_key)
        assert anchor.sticky_key is not None
        assert anchor.outcome == DERIVATION_OUTCOME_ANCHOR_NEW


class _FakeClock:
    """Minimal ``Clock`` seam: only ``monotonic`` is read by the index."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def time(self) -> float:
        return self.value

    def now(self) -> datetime:
        return _NOW


class TestThreadAnchorIndexBounds:
    def _window(self, index: int):
        domain = thread_anchor_domain(api_key_id="ak", model_class="std", instructions="i")
        window = build_thread_window(_json_value([_user(f"a{index}"), _user(f"b{index}")]), domain=domain)
        assert window is not None
        return window

    def test_anchor_and_index_caps_hold_under_many_threads(self):
        index = ThreadAnchorIndex(max_anchors=8, max_index_digests=16, max_candidates_per_digest=2)
        for i in range(500):
            index.register(f"thread-{i}", self._window(i))
        assert len(index) == 8
        assert index.index_size <= 16

    def test_process_index_respects_its_default_caps(self):
        from app.modules.proxy.thread_anchors import _MAX_ANCHORS, _MAX_INDEX_DIGESTS

        api_key = _make_api_key()
        for i in range(_MAX_ANCHORS + 200):
            _derive_prompt_cache_key(_request([_env_item(f"/repo/{i}"), _user(f"task {i}")]), api_key)
        index = get_thread_anchor_index()
        assert len(index) <= _MAX_ANCHORS
        assert index.index_size <= _MAX_INDEX_DIGESTS

    def test_eviction_drops_the_least_recently_used_anchor(self):
        clock = _FakeClock()
        index = ThreadAnchorIndex(max_anchors=2, max_index_digests=64, max_candidates_per_digest=4, clock=clock)
        window_a, window_b, window_c = self._window(1), self._window(2), self._window(3)
        index.register("a", window_a)
        clock.value = 1.0
        index.register("b", window_b)
        clock.value = 2.0
        assert index.lookup(window_a, ttl_seconds=100.0) == "a"
        clock.value = 3.0
        index.register("c", window_c)
        clock.value = 4.0
        # "b" was the least recently used at eviction time.
        assert index.lookup(window_b, ttl_seconds=100.0) is None
        assert index.lookup(window_a, ttl_seconds=100.0) == "a"

    def test_anchor_expires_at_the_ttl(self):
        clock = _FakeClock()
        index = ThreadAnchorIndex(clock=clock)
        window = self._window(1)
        index.register("a", window)
        clock.value = 1799.0
        assert index.lookup(window, ttl_seconds=1800.0) == "a"
        clock.value = 1800.0
        assert index.lookup(window, ttl_seconds=1800.0) is None
        assert len(index) == 0

    def test_window_is_bounded_by_item_and_total_encoding_caps(self):
        domain = thread_anchor_domain(api_key_id="ak", model_class="std", instructions="i")
        huge = [_user("z" * (4 * _MAX_ITEM_ENCODED_CHARS)) for _ in range(40)]
        window = build_thread_window(_json_value(huge), domain=domain)
        assert window is not None
        assert window.item_count <= 32
        # The byte ceiling stops the walk well before the item ceiling here.
        assert window.item_count < 32
