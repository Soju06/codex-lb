"""The client's ``store`` intent on the overflow path (#2123 WP-C2 integration, design §7.2 anchors).

``ResponsesRequest.store`` is forced to ``False`` for the ChatGPT backend. The
overflow dispatch needs the client's own value twice: the anchor rule (anchor
iff the client did not send ``store: false``) and the source-direction body (a
source honours the client's storage intent, which is what an SDK
``previous_response_id`` chain resolves against). ``normalize_responses_request_payload``
captures the raw value; nothing on the ChatGPT-bound serialization changes.
"""

from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.model_sources.projection import overflow_opaque_value, strip_source_telemetry
from app.modules.proxy.overflow import (
    _source_body,
    anchor_requested,
    client_store_intent,
    overflow_source_wire_body,
    restore_client_store,
)
from app.modules.proxy.request_policy import normalize_responses_request_payload

_BODY: dict[str, JsonValue] = {
    "model": "gpt-5.4",
    "instructions": "You are a test.",
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    "stream": True,
}


def _validate(*, store: bool | None, openai_compat: bool) -> ResponsesRequest:
    body: dict[str, JsonValue] = dict(_BODY)
    if store is not None:
        body["store"] = store
    return normalize_responses_request_payload(body, openai_compat=openai_compat)


@pytest.mark.parametrize("openai_compat", [False, True], ids=["codex_shape", "openai_compat_shape"])
@pytest.mark.parametrize("store", [None, True, False], ids=["omitted", "true", "false"])
def test_normalization_captures_the_client_store_and_keeps_the_chatgpt_field_false(
    store: bool | None, openai_compat: bool
) -> None:
    payload = _validate(store=store, openai_compat=openai_compat)

    assert client_store_intent(payload) is store
    assert anchor_requested(payload) is (store is not False)
    # The ChatGPT-bound serializations are untouched: ``store`` stays the forced ``False``.
    assert payload.store is False
    assert payload.model_dump_for_forwarding()["store"] is False
    assert payload.to_payload().get("store", False) is False


@pytest.mark.parametrize("store", [None, True, False], ids=["omitted", "true", "false"])
def test_restore_client_store_puts_the_client_value_on_the_source_body(store: bool | None) -> None:
    payload = _validate(store=store, openai_compat=True)
    body = payload.model_dump_for_forwarding()
    assert body["store"] is False

    restored = restore_client_store(body, payload)

    assert restored is body
    if store is None:
        assert "store" not in restored, "an omitted ``store`` leaves the source its own default"
    else:
        assert restored["store"] is store


def test_uncaptured_payload_trusts_only_an_explicit_false() -> None:
    # A payload validated outside the route normalizer (no capture): a client value the validator
    # collapsed can only be trusted as ``False``; an absent field is the API default.
    sent_false = ResponsesRequest.model_validate({**_BODY, "store": False})
    sent_true = ResponsesRequest.model_validate({**_BODY, "store": True})
    omitted = ResponsesRequest.model_validate(dict(_BODY))

    assert client_store_intent(sent_false) is False and anchor_requested(sent_false) is False
    assert client_store_intent(sent_true) is False and anchor_requested(sent_true) is False
    assert client_store_intent(omitted) is None and anchor_requested(omitted) is True
    assert "store" not in restore_client_store(omitted.model_dump_for_forwarding(), omitted)
    assert restore_client_store(sent_true.model_dump_for_forwarding(), sent_true)["store"] is False


def test_explicit_null_store_is_read_conservatively_as_false() -> None:
    # ``store: null`` is not a boolean the API accepts; the validator collapses it like any client value, the
    # capture records nothing, and the ``model_fields_set`` fallback trusts only ``False``: no anchor, and
    # the source receives ``store: false``.
    body: dict[str, JsonValue] = {**_BODY, "store": None}
    payload = normalize_responses_request_payload(body, openai_compat=True)
    assert client_store_intent(payload) is False
    assert anchor_requested(payload) is False
    assert restore_client_store(payload.model_dump_for_forwarding(), payload)["store"] is False


# --- the overflow-only wire shaping (#2123) -----------------------------------------------------


def test_the_classified_body_and_the_wire_body_are_the_same_projection() -> None:
    """One function, two call sites: what the portability verdict judged is what leaves.

    ``overflow._source_body`` builds the body the verdict classifies and
    ``api._source_responses_response`` builds the body the source receives.
    They are separate dicts, so the only thing that keeps them honest is that
    both end in ``overflow_source_wire_body`` -- asserted here rather than
    assumed, because a drift between them is exactly how a neutralised value
    stops being the value that leaves.
    """

    payload = _validate(store=None, openai_compat=True)

    classified = _source_body(payload, namespace="key_1")
    wire = overflow_source_wire_body(
        strip_source_telemetry(payload.model_dump_for_forwarding(), strip_service_tier=True),
        payload,
        namespace="key_1",
    )

    assert classified == wire


@pytest.mark.parametrize("namespace", [None, "key_1"])
def test_the_wire_body_carries_the_neutralised_identifiers_and_not_the_clients(namespace: str | None) -> None:
    body: dict[str, JsonValue] = {
        **_BODY,
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": "01a099e8-25c2-7e30-bd5d-b1e522c07985",
    }
    payload = normalize_responses_request_payload(body, openai_compat=True)

    wire = _source_body(payload, namespace=namespace)

    assert "include" not in wire
    assert wire["prompt_cache_key"] == overflow_opaque_value(
        "01a099e8-25c2-7e30-bd5d-b1e522c07985", namespace=namespace, domain="prompt_cache"
    )
    # Direct source routing is the control: it still forwards what the client sent.
    direct = strip_source_telemetry(payload.model_dump_for_forwarding(), strip_service_tier=False)
    assert direct["include"] == ["reasoning.encrypted_content"]
    assert direct["prompt_cache_key"] == "01a099e8-25c2-7e30-bd5d-b1e522c07985"
