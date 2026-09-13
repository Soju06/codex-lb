from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.db.models import HttpBridgeSessionState
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import observability as observability_module
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers
from app.modules.proxy._service.http_bridge import streaming as http_bridge_streaming_module
from app.modules.proxy._service.response_create import _fingerprint_input_items
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup

pytestmark = pytest.mark.unit

_STORED_ITEMS: list[JsonValue] = [
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "first"}]},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
]
_FRESH_ITEM: JsonValue = {
    "type": "message",
    "role": "user",
    "content": [{"type": "input_text", "text": "second"}],
}


def _payload(input_value: object) -> ResponsesRequest:
    return ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "be brief", "input": input_value})


def _lookup(
    *,
    latest_input_item_count: int | None,
    latest_input_full_fingerprint: str | None,
) -> DurableBridgeLookup:
    return DurableBridgeLookup(
        session_id="session-1",
        canonical_kind="thread_header",
        canonical_key="thread-1",
        api_key_scope="global",
        account_id="account-1",
        owner_instance_id="codex-lb",
        owner_epoch=1,
        lease_expires_at=None,
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id="resp-1",
        latest_input_item_count=latest_input_item_count,
        latest_input_full_fingerprint=latest_input_full_fingerprint,
    )


def _matching_lookup() -> DurableBridgeLookup:
    return _lookup(
        latest_input_item_count=len(_STORED_ITEMS),
        latest_input_full_fingerprint=_fingerprint_input_items(list(_STORED_ITEMS)),
    )


def _full_resend_payload() -> ResponsesRequest:
    return _payload([*_STORED_ITEMS, _FRESH_ITEM])


def _anchor_rejection(
    payload: ResponsesRequest,
    lookup: DurableBridgeLookup,
    *,
    payload_looks_like_full_resend: bool = True,
) -> str | None:
    return http_bridge_streaming_module._durable_full_resend_anchor_rejection(
        payload,
        lookup,
        payload_looks_like_full_resend=payload_looks_like_full_resend,
    )


def test_matching_full_resend_has_no_anchor_rejection() -> None:
    assert _anchor_rejection(_full_resend_payload(), _matching_lookup()) is None


def test_non_full_resend_payload_is_reported_distinctly() -> None:
    # The body carries only the new turn, so there is no history to move.
    assert (
        _anchor_rejection(
            _payload([_FRESH_ITEM]),
            _matching_lookup(),
            payload_looks_like_full_resend=False,
        )
        == "payload_not_full_resend"
    )


def test_row_without_stored_count_is_reported_distinctly() -> None:
    # A durable row written before the anchor metadata existed, or one whose
    # owner was pinned from the request-log index.
    assert (
        _anchor_rejection(
            _full_resend_payload(),
            _lookup(latest_input_item_count=None, latest_input_full_fingerprint="deadbeef"),
        )
        == "anchor_metadata_missing"
    )


def test_diverged_prefix_is_reported_distinctly() -> None:
    assert (
        _anchor_rejection(
            _full_resend_payload(),
            _lookup(
                latest_input_item_count=len(_STORED_ITEMS),
                latest_input_full_fingerprint="0" * 64,
            ),
        )
        == "prefix_fingerprint_mismatch"
    )


def test_missing_fingerprint_is_a_prefix_mismatch() -> None:
    assert (
        _anchor_rejection(
            _full_resend_payload(),
            _lookup(latest_input_item_count=len(_STORED_ITEMS), latest_input_full_fingerprint=None),
        )
        == "prefix_fingerprint_mismatch"
    )


def test_stored_prefix_without_a_fresh_suffix_is_a_prefix_mismatch() -> None:
    # The stored prefix consumes the whole body, so nothing new was sent.
    assert _anchor_rejection(_payload(list(_STORED_ITEMS)), _matching_lookup()) == "prefix_fingerprint_mismatch"


def test_string_input_refuses_on_the_prefix_proof() -> None:
    # A non-list body cannot match a stored prefix, so the prefix proof always
    # refuses first. ``input_not_itemized`` therefore stays a guard rather than
    # an observable outcome: it is only reachable if some future caller admits
    # a non-list body past the prefix proof.
    assert (
        _anchor_rejection(
            _payload("plain string input"),
            _matching_lookup(),
            payload_looks_like_full_resend=True,
        )
        == "prefix_fingerprint_mismatch"
    )


def test_every_anchor_rejection_is_in_the_closed_set() -> None:
    matching = _matching_lookup()
    observed = {
        _anchor_rejection(_payload([_FRESH_ITEM]), matching, payload_looks_like_full_resend=False),
        _anchor_rejection(
            _full_resend_payload(),
            _lookup(latest_input_item_count=None, latest_input_full_fingerprint="deadbeef"),
        ),
        _anchor_rejection(
            _full_resend_payload(),
            _lookup(latest_input_item_count=len(_STORED_ITEMS), latest_input_full_fingerprint="0" * 64),
        ),
    }
    assert observed <= http_bridge_streaming_module.ACCOUNT_NEUTRAL_REPLAY_REJECTIONS
    # The three causes that used to collapse into one ``False``.
    assert len(observed) == 3


def test_closed_reason_set_matches_the_literal() -> None:
    assert http_bridge_streaming_module.ACCOUNT_NEUTRAL_REPLAY_REJECTIONS == frozenset(
        {
            "file_bound",
            "no_durable_lookup",
            "payload_not_full_resend",
            "anchor_metadata_missing",
            "prefix_fingerprint_mismatch",
            "input_not_itemized",
            "missing_prior_output",
            "account_scoped_input",
        }
    )


class _RecordingCounter:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.increments = 0

    def labels(self, **kwargs: str) -> "_RecordingCounter":
        self.calls.append(kwargs)
        return self

    def inc(self, amount: float = 1) -> None:
        self.increments += 1


def test_replay_rejection_counter_uses_the_closed_label_set(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _RecordingCounter()
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_replay_rejected_total", counter, raising=False)

    observability_module._record_continuity_replay_rejected(
        surface="http_bridge",
        reason="no_durable_lookup",
    )

    assert counter.calls == [{"surface": "http_bridge", "reason": "no_durable_lookup"}]
    assert counter.increments == 1


def test_replay_rejection_recorder_no_ops_without_prometheus(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _RecordingCounter()
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", False)
    monkeypatch.setattr(proxy_service, "continuity_replay_rejected_total", counter, raising=False)

    observability_module._record_continuity_replay_rejected(surface="http_bridge", reason="file_bound")

    assert counter.calls == []
    assert counter.increments == 0


def test_replay_rejection_event_is_logged_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.modules.proxy.service")
    key = proxy_service._HTTPBridgeSessionKey("thread_header", "thread-1", None)

    http_bridge_helpers._log_http_bridge_event(
        "owner_unavailable_replay_rejected",
        key,
        account_id="account-1",
        model="gpt-5.4",
        detail="reason=no_durable_lookup",
        cache_key_family="thread_header",
        owner_check_applied=True,
    )

    records = [record for record in caplog.records if "owner_unavailable_replay_rejected" in record.getMessage()]
    assert len(records) == 1
    # A refusal nobody can find in the logs is the failure this change exists
    # to fix, so the level is part of the contract.
    assert records[0].levelno == logging.WARNING
    assert "reason=no_durable_lookup" in records[0].getMessage()
    assert "account_id=account-1" in records[0].getMessage()
    assert "key_strength=hard" in records[0].getMessage()


def test_lookup_field_rename_fails_here() -> None:
    # The anchor proof reads only these two lookup fields, so a rename must
    # fail here rather than silently degrade every refusal to one reason.
    lookup = replace(_matching_lookup(), latest_input_item_count=None)
    assert _anchor_rejection(_full_resend_payload(), lookup) == "anchor_metadata_missing"
