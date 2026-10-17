"""Behaviour of the thread-anchored ``prompt_cache_key`` derivation.

These tests assert what the key must *do* for an unanchored HTTP thread --
stay put while the transcript grows, stay put when the client trims its
leading history, never merge two distinct threads, and say so honestly when
there is nothing to anchor -- rather than the literal shape of the key.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypedDict, cast

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
    _prompt_cache_key_from_request_model,
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


def _request(
    items: Sequence[object], *, model: str = "gpt-5.4", instructions: str = "You are Codex"
) -> ResponsesRequest:
    return ResponsesRequest(model=model, instructions=instructions, input=_json_value(items))


def _env_item(cwd: str = "/repo") -> dict[str, object]:
    return {"role": "user", "content": [{"type": "input_text", "text": f"<environment_context>cwd={cwd}"}]}


def _user(text: str) -> dict[str, object]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant(text: str) -> dict[str, object]:
    return {"role": "assistant", "content": [{"type": "output_text", "text": text}]}


class TestAppendStability:
    def test_key_is_stable_across_a_growing_transcript(self):
        """One mint while the transcript is below the acceptance floor, then held.

        A two-item opening is below ``_MIN_OVERLAP_ITEMS`` (see
        ``thread_anchors``: one or two shared leading items are routinely
        generic), so its key is not carried into the second turn. From four
        items on, the key never moves again.
        """
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("build a server")]
        _derive_prompt_cache_key(_request(items), api_key)
        items = [*items, _assistant("reply 0"), _user("follow-up 0")]
        first = _derive_prompt_cache_key(_request(items), api_key)
        for turn in range(1, 30):
            items = [*items, _assistant(f"reply {turn}"), _user(f"follow-up {turn}")]
            anchor = _derive_prompt_cache_anchor(_request(items), api_key)
            assert anchor.sticky_key == first, f"key moved on turn {turn}"
            assert anchor.outcome == DERIVATION_OUTCOME_ANCHOR_HIT

    def test_transcript_longer_than_the_retained_window_stays_stable(self):
        """The window slides; the anchor does not."""
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("start"), _assistant("a0"), _user("u0")]
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

    def test_a_one_item_opening_does_not_carry_its_key_into_the_second_turn(self):
        """The plain SDK shape: turn one is a single user item.

        One shared item is the weakest possible evidence and is exactly what
        parallel workers of one agent share, so it must not transfer a key --
        not even to the same thread's next turn. The thread anchors from the
        turn where its transcript reaches ``_MIN_OVERLAP_ITEMS`` items.
        """
        api_key = _make_api_key()
        first = _derive_prompt_cache_anchor(_request([_user("build a server")]), api_key)
        assert first.sticky_key is not None
        items: list[object] = [_user("build a server"), _assistant("here"), _user("add logging")]
        second = _derive_prompt_cache_anchor(_request(items), api_key)
        assert second.sticky_key != first.sticky_key
        # Five items recorded, so the next turn clears the four-item floor.
        items = [*items, _assistant("done"), _user("now tests")]
        third = _derive_prompt_cache_anchor(_request(items), api_key)
        items = [*items, _assistant("green"), _user("ship it")]
        fourth = _derive_prompt_cache_anchor(_request(items), api_key)
        assert fourth.sticky_key == third.sticky_key
        assert fourth.outcome == DERIVATION_OUTCOME_ANCHOR_HIT

    def test_a_one_item_tail_alignment_is_not_enough_to_merge(self):
        """Partial alignment needs four items; one shared item must not merge."""
        api_key = _make_api_key()
        established = _derive_prompt_cache_key(
            _request([_env_item(), _user("u0"), _assistant("a0"), _user("continue")]), api_key
        )
        # A fresh thread whose opening item equals the other thread's last item.
        other = _derive_prompt_cache_key(_request([_user("continue"), _assistant("x"), _user("y")]), api_key)
        assert other != established

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
        thread_a: list[object] = [_env_item(), _user("refactor the parser"), _assistant("a0"), _user("a-follow 0")]
        thread_b: list[object] = [_env_item(), _user("write the release notes"), _assistant("b0"), _user("b-follow 0")]
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
        assert mini is not None and codex is not None and std is not None
        assert "-mini-" in mini
        assert "-codex-" in codex
        assert "-std-" in std

    def test_random_independent_sequences_never_merge(self):
        """Property-style: appends never move a key, and threads never cross."""
        api_key = _make_api_key()
        rng = random.Random(20260911)
        threads: list[list[object]] = [
            [_env_item(), _user(f"seed-{index}"), _assistant(f"ack-{index}"), _user(f"go-{index}")]
            for index in range(12)
        ]
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
        assert second.outcome == DERIVATION_OUTCOME_UNANCHORABLE
        # No key at all -- not a fresh uuid, and not a constant placeholder
        # that a second resolution would read back as client-supplied.
        assert first.sticky_key is None
        assert second.sticky_key is None

    def test_non_list_input_is_unanchorable(self):
        """`ResponsesRequest` normalises a bare string into a one-item list, so
        this guards the defensive branch for anything that is not a list."""
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        assert build_thread_window(_json_value("hello world"), domain=domain) is None
        assert build_thread_window(_json_value(None), domain=domain) is None

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
        # And nothing is written onto the payload, so a second resolution of
        # this same object cannot read a proxy value back as client-supplied.
        assert _prompt_cache_key_from_request_model(payload) is None

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
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
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

    def test_window_is_bounded_by_the_total_encoding_ceiling(self):
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        big = [_user("z" * 64 * 1024) for _ in range(40)]
        window = build_thread_window(_json_value(big), domain=domain)
        assert window is not None
        assert window.item_count <= 32
        # The byte ceiling stops the walk well before the item ceiling here.
        assert window.item_count < 32

    def test_an_item_past_the_per_item_ceiling_is_never_digested(self):
        """Never digest a truncated item: that is the collision being removed.

        It ends the window instead of poisoning the body -- see
        ``TestOversizedItemDoesNotPoisonTheThread`` -- so the two items here
        yield a one-item window, and an oversized *last* item yields none.
        """
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        oversized = [_user("z" * (_MAX_ITEM_ENCODED_CHARS + 1)), _user("tail")]
        bounded = build_thread_window(_json_value(oversized), domain=domain)
        assert bounded is not None
        assert bounded.item_count == 1
        trailing = [_user("head"), _user("z" * (_MAX_ITEM_ENCODED_CHARS + 1))]
        assert build_thread_window(_json_value(trailing), domain=domain) is None


class TestUnanchorableKeyIsNeverPromoted:
    """A body with nothing to anchor must never acquire a sticky key.

    The same payload object is resolved twice on the real
    `_stream_via_http_bridge` -> `_stream_with_retry` fallback. If the first
    resolution writes a proxy-minted placeholder onto the payload, the second
    one reads it back through the client-supplied branch and returns a constant
    per-API-key string as a PROMPT_CACHE sticky key, collapsing every
    unanchorable thread of that API key onto one row and one account.
    """

    # A plain dict literal infers `bool | int` for its values, so `**self._KWARGS`
    # widens every keyword to that union and the checker rejects the call. A
    # TypedDict keeps one type per key through the unpacking.
    class _Kwargs(TypedDict):
        codex_session_affinity: bool
        openai_cache_affinity: bool
        openai_cache_affinity_max_age_seconds: int
        sticky_threads_enabled: bool

    _KWARGS: _Kwargs = {
        "codex_session_affinity": True,
        "openai_cache_affinity": True,
        "openai_cache_affinity_max_age_seconds": 1800,
        "sticky_threads_enabled": True,
    }

    def test_re_resolving_one_unanchorable_payload_never_yields_a_sticky_key(self):
        payload = _request([], instructions="")
        api_key = _make_api_key()
        first = _resolve_prompt_cache_key(payload, openai_cache_affinity=True, api_key=api_key)
        second = _resolve_prompt_cache_key(payload, openai_cache_affinity=True, api_key=api_key)
        assert first.sticky_key is None
        assert second.sticky_key is None
        assert second.source != "payload"
        assert second.outcome == DERIVATION_OUTCOME_UNANCHORABLE

    def test_unanchorable_resolution_does_not_mutate_the_payload(self):
        payload = _request([], instructions="")
        _resolve_prompt_cache_key(payload, openai_cache_affinity=True, api_key=_make_api_key())
        assert _prompt_cache_key_from_request_model(payload) is None

    def test_second_affinity_pass_of_an_unanchorable_body_writes_no_sticky_row(self):
        payload = _request([], instructions="")
        api_key = _make_api_key()
        first = _sticky_key_for_responses_request(payload, {}, api_key=api_key, **self._KWARGS)
        second = _sticky_key_for_responses_request(payload, {}, api_key=api_key, **self._KWARGS)
        assert first.key is None
        assert second.key is None
        assert second.prompt_cache_derivation_outcome == DERIVATION_OUTCOME_UNANCHORABLE

    def test_unanchorable_threads_of_one_api_key_do_not_share_a_sticky_key(self):
        api_key = _make_api_key()
        payloads = [_request([], instructions="") for _ in range(3)]
        for payload in payloads:
            _sticky_key_for_responses_request(payload, {}, api_key=api_key, **self._KWARGS)
        keys = {
            _sticky_key_for_responses_request(payload, {}, api_key=api_key, **self._KWARGS).key for payload in payloads
        }
        assert keys == {None}


class TestLargeTrailingItemsDoNotCollapseTheWindow:
    """The p90 453k-token cohort this change exists to serve.

    A trailing item at or past the whole-window byte ceiling used to end the
    backward walk after one item, so the recorded window was "the newest item"
    and differed every turn: a new key every turn, reported as a benign
    `anchor_reset`.
    """

    @pytest.mark.parametrize("item_chars", [96 * 1024, 256 * 1024])
    def test_ten_turns_of_large_items_hold_one_key(self, item_chars: int):
        api_key = _make_api_key()
        filler = "z" * item_chars
        items: list[object] = [_env_item(), _user(f"open-{filler}")]
        keys: list[str | None] = []
        for turn in range(10):
            items = [*items, _assistant(f"a{turn}-{filler}"), _user(f"u{turn}-{filler}")]
            anchor = _derive_prompt_cache_anchor(_request(items), api_key)
            keys.append(anchor.sticky_key)
        assert None not in keys
        assert len(set(keys)) == 1, f"{len(set(keys))} keys in 10 turns at {item_chars} chars per item"

    @pytest.mark.parametrize("item_chars", [96 * 1024, 256 * 1024])
    def test_window_keeps_the_documented_item_floor(self, item_chars: int):
        from app.modules.proxy.thread_anchors import _MIN_WINDOW_ITEMS

        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        items = [_user(f"{index}-" + "z" * item_chars) for index in range(20)]
        window = build_thread_window(_json_value(items), domain=domain)
        assert window is not None
        assert window.item_count >= _MIN_WINDOW_ITEMS


class TestOversizedItemDoesNotPoisonTheThread:
    """One item past the per-item ceiling used to make ~16 turns unanchorable.

    The reversed walk kept reaching it for as many turns as the window is
    deep, so `sticky_key` stayed `None` and every one of those turns forwarded
    unbound.
    """

    def test_an_oversized_item_bounds_the_window_instead_of_poisoning_the_body(self):
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        items = [
            _user("z" * (_MAX_ITEM_ENCODED_CHARS + 1)),
            _user("a"),
            _user("b"),
            _user("c"),
            _user("d"),
        ]
        window = build_thread_window(_json_value(items), domain=domain)
        assert window is not None
        assert window.item_count == 4

    def test_a_thread_re_anchors_within_a_few_turns_of_an_oversized_item(self):
        api_key = _make_api_key()
        items: list[object] = [_env_item(), _user("start"), _assistant("ok"), _user("go")]
        _derive_prompt_cache_key(_request(items), api_key)
        items = [*items, _assistant("z" * (_MAX_ITEM_ENCODED_CHARS + 1))]
        anchors = []
        for turn in range(10):
            items = [*items, _user(f"u{turn}"), _assistant(f"a{turn}")]
            anchors.append(_derive_prompt_cache_anchor(_request(items), api_key))
        tail = anchors[-6:]
        assert all(anchor.sticky_key is not None for anchor in tail)
        assert len({anchor.sticky_key for anchor in tail}) == 1


class TestSharedPreambleNeverTransfersAKey:
    """`never merge two genuinely distinct threads onto one key`.

    The whole-window-consumed acceptance branch let a sibling whose opening
    items equal another thread's entire recorded window claim that thread's
    key, and the sibling's `register` then overwrote the window, so the
    original thread permanently lost the key it owned.
    """

    def test_a_two_item_shared_opening_does_not_transfer_the_key(self):
        api_key = _make_api_key()
        preamble = [_env_item(), _user("<user_instructions>read AGENTS.md")]
        key_a = _derive_prompt_cache_key(_request(preamble), api_key)
        key_b = _derive_prompt_cache_key(_request([*preamble, _user("worker B task")]), api_key)
        assert key_b != key_a

    def test_a_one_item_shared_opening_does_not_transfer_the_key(self):
        api_key = _make_api_key()
        opening = [_env_item()]
        key_a = _derive_prompt_cache_key(_request(opening), api_key)
        key_b = _derive_prompt_cache_key(_request([*opening, _assistant("ok"), _user("worker B")]), api_key)
        assert key_b != key_a

    def test_thread_a_never_loses_a_key_it_owns_to_a_shared_opening(self):
        api_key = _make_api_key()
        preamble = [_env_item(), _user("<user_instructions>read AGENTS.md")]
        key_a = _derive_prompt_cache_key(_request(preamble), api_key)
        for worker in range(4):
            _derive_prompt_cache_key(_request([*preamble, _user(f"worker {worker}")]), api_key)
        assert _derive_prompt_cache_key(_request(preamble), api_key) == key_a

    def test_an_established_thread_keeps_its_anchor_while_siblings_share_its_preamble(self):
        api_key = _make_api_key()
        preamble = [_env_item(), _user("<user_instructions>read AGENTS.md")]
        items: list[object] = [*preamble, _assistant("a0"), _user("u1")]
        _derive_prompt_cache_key(_request(items), api_key)
        items = [*items, _assistant("a1"), _user("u2")]
        owned = _derive_prompt_cache_anchor(_request(items), api_key)
        assert owned.outcome == DERIVATION_OUTCOME_ANCHOR_HIT
        for worker in range(5):
            sibling = _derive_prompt_cache_key(_request([*preamble, _user(f"worker {worker}")]), api_key)
            assert sibling != owned.sticky_key
        items = [*items, _assistant("a2"), _user("u3")]
        again = _derive_prompt_cache_anchor(_request(items), api_key)
        assert again.sticky_key == owned.sticky_key
        assert again.outcome == DERIVATION_OUTCOME_ANCHOR_HIT


class TestIndexReferencesFollowTheirAnchors:
    """A dead thread key must not keep occupying reverse-index candidate slots.

    Rows are capped at ``_MAX_CANDIDATES_PER_DIGEST``, so a stale reference on
    a popular digest can crowd a live anchor out of its row before that anchor
    is ever verified.
    """

    def _window(self, index: int):
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        window = build_thread_window(_json_value([_user(f"a{index}"), _user(f"b{index}")]), domain=domain)
        assert window is not None
        return window

    def test_evicting_an_anchor_drops_its_reverse_index_rows(self):
        clock = _FakeClock()
        index = ThreadAnchorIndex(max_anchors=2, max_index_digests=1024, max_candidates_per_digest=8, clock=clock)
        index.register("a", self._window(1))
        index.register("b", self._window(2))
        index.register("c", self._window(3))
        assert len(index) == 2
        # Two digests each for the two surviving anchors, none for the evicted.
        assert index.index_size == 4

    def test_re_registering_a_thread_drops_its_previous_window_rows(self):
        index = ThreadAnchorIndex()
        index.register("a", self._window(1))
        index.register("a", self._window(2))
        assert index.index_size == 2
        assert index.lookup(self._window(1), ttl_seconds=1800.0) is None
        assert index.lookup(self._window(2), ttl_seconds=1800.0) == "a"


class TestPerItemCeilingIsEnforcedBeforeEncoding:
    def test_an_oversized_item_is_refused_without_encoding_it(self, monkeypatch):
        """`iterencode` yields a large string scalar as one materialised chunk.

        Checking the size only inside the encode loop means allocating the very
        encoding the walk is about to refuse.
        """
        from app.modules.proxy import thread_anchors

        encoded: list[object] = []
        real_iterencode = thread_anchors._ITEM_ENCODER.iterencode

        def spy(value):
            encoded.append(value)
            return real_iterencode(value)

        monkeypatch.setattr(thread_anchors._ITEM_ENCODER, "iterencode", spy)
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        items = [_user("z" * (_MAX_ITEM_ENCODED_CHARS + 1)), _user("a"), _user("b"), _user("c")]
        window = build_thread_window(_json_value(items), domain=domain)
        assert window is not None
        assert window.item_count == 3
        assert len(encoded) == 3, "the oversized item was handed to the encoder anyway"


class TestNonAsciiItemsAreNotExpandedSixfold:
    """`ensure_ascii=True` renders every non-ASCII code point as `\\uXXXX`.

    On a fleet whose transcripts are largely Korean and Japanese that both
    materialises megabytes for an item the walk may be about to refuse, and
    trips the per-item ceiling at roughly a sixth of its documented size --
    turning ordinary items into window boundaries.
    """

    def test_a_large_non_ascii_item_is_digested_not_refused(self):
        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        # Comfortably under the per-item ceiling as written, ~6x over it when
        # every code point is escaped.
        korean = "안녕하세요 " * 40_000
        assert len(korean) < _MAX_ITEM_ENCODED_CHARS
        window = build_thread_window(_json_value([_user(korean), _user("tail")]), domain=domain)
        assert window is not None
        assert window.item_count == 2

    def test_encoded_size_tracks_the_raw_size_for_non_ascii_text(self):
        from app.modules.proxy.thread_anchors import _bounded_item_digest

        domain = thread_anchor_domain(owner_row_id="ak", model_class="std", instructions="i")
        item = _json_value(_user("한" * 100_000))
        digested = _bounded_item_digest(domain, item)
        assert isinstance(digested, tuple)
        _digest, encoded_size = digested
        assert encoded_size < 2 * 100_000, f"non-ASCII text expanded to {encoded_size} chars"
