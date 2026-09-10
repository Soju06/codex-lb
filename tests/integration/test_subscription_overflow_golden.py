"""Ship-dark golden: with both settings columns ``NULL`` the request path is a byte-identical no-op (#2123 WP-C2, I9).

Oracle: **stub equivalence**. The same request runs twice on the same app --
once with the real ``resolve_subscription_overflow`` (both
``dashboard_settings`` columns ``NULL``) and once with the decision module's
entry points replaced by inert stubs (``return None`` / identity) -- and the
two answers must agree on status, headers (minus the per-request volatile
ones) and body bytes, for the three answers the subscription path gives:
today's exhausted-pool ``429 usage_limit_reached``, a subscription SSE stream
(native Codex and SDK shaping) and a ``/v1/responses`` non-stream JSON.

The 429 body is additionally pinned to a committed literal so any change in
the wording of today's answer is visible in review, and a spy test proves the
structural half of the guarantee: unconfigured, the request path performs no
probe, no pin lookup, no source selection, no portability walk and no
admission claim.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from app.modules.model_sources import projection as projection_module
from app.modules.model_sources import selection as selection_module
from app.modules.proxy import api as proxy_api
from app.modules.proxy import model_source_pins as pins_module
from app.modules.proxy import overflow as overflow_module
from app.modules.proxy import source_admission as admission_module
from app.modules.proxy._load_balancer import exhaustion_probe as probe_module
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED
from tests.integration.test_subscription_overflow_routing import (
    CODEX_ROUTE,
    V1_ROUTE,
    _canned_subscription_stream,
    _codex_body,
    _import_account,
    _native_headers,
    _seed_exhausted_pool,
)

pytestmark = pytest.mark.integration

# Per-request values that legitimately differ between two otherwise identical answers.
_VOLATILE_HEADERS = frozenset({"date", "x-request-id", "x-codex-turn-state"})

Snapshot = tuple[int, dict[str, str], bytes]


def _snapshot(response: Any) -> Snapshot:
    headers = {name.lower(): value for name, value in response.headers.items() if name.lower() not in _VOLATILE_HEADERS}
    # Presence of the synthesized turn-state header is part of the contract even though its value is random.
    headers["<has x-codex-turn-state>"] = str("x-codex-turn-state" in {name.lower() for name in response.headers})
    return response.status_code, headers, response.content


def _inert_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-C2 behaviour: the decision never runs, the hint hook is the identity, compaction is never denied."""

    async def no_decision(*_args: object, **_kwargs: object) -> None:
        return None

    def identity(_request: object, content: Any, headers: Any) -> tuple[Any, Any]:
        return content, headers

    monkeypatch.setattr(proxy_api, "resolve_subscription_overflow", no_decision)
    monkeypatch.setattr(proxy_api, "compact_pin_denial", no_decision)
    monkeypatch.setattr(proxy_api, "apply_usage_limit_hint", identity)


async def _twice(
    async_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    send: Callable[[], Any],
) -> tuple[Snapshot, Snapshot]:
    """(real decision, inert stubs) snapshots of the same request on the same app."""

    real = _snapshot(await send())
    with pytest.MonkeyPatch.context() as inert:
        _inert_stubs(inert)
        stubbed = _snapshot(await send())
    del monkeypatch
    return real, stubbed


def _golden_429_bytes(reset_at: int) -> bytes:
    """Today's exhausted-pool answer, byte for byte (``JSONResponse`` compact separators)."""

    return json.dumps(
        {
            "error": {
                "message": "Rate limit exceeded. Try again in 300s",
                "type": USAGE_LIMIT_REACHED,
                "code": USAGE_LIMIT_REACHED,
                "resets_at": reset_at,
            }
        },
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_exhausted_pool_429_is_byte_identical_to_the_inert_answer_and_the_committed_golden(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_at = await _seed_exhausted_pool(async_client, tag="golden_429")
    body = _codex_body()
    headers = _native_headers("thr_golden_429", session_id="sess_golden_429")

    real, stubbed = await _twice(
        async_client, monkeypatch, lambda: async_client.post(CODEX_ROUTE, json=body, headers=headers)
    )

    assert real == stubbed
    status, response_headers, content = real
    assert status == 429
    assert content == _golden_429_bytes(reset_at)
    assert "x-codex-promo-message" not in response_headers
    assert "retry-after" not in response_headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (CODEX_ROUTE, _native_headers("thr_golden_stream", session_id="sess_golden_stream")),
        (V1_ROUTE, {"user-agent": "openai-python/1.99"}),
    ],
    ids=["codex-native", "v1-sdk"],
)
async def test_subscription_stream_is_byte_identical_to_the_inert_answer(
    async_client, monkeypatch: pytest.MonkeyPatch, path: str, headers: dict[str, str]
) -> None:
    await _import_account(async_client, "acc_golden_stream", "golden-stream@example.com")
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_golden_stream")
    body = _codex_body()

    real, stubbed = await _twice(async_client, monkeypatch, lambda: async_client.post(path, json=body, headers=headers))

    assert real == stubbed
    status, _response_headers, content = real
    assert status == 200
    assert b"response.completed" in content
    assert len(relayed) == 2


@pytest.mark.asyncio
async def test_v1_non_stream_is_byte_identical_to_the_inert_answer(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import_account(async_client, "acc_golden_json", "golden-json@example.com")
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_golden_json")
    body = {**_codex_body(), "stream": False}

    real, stubbed = await _twice(
        async_client,
        monkeypatch,
        lambda: async_client.post(V1_ROUTE, json=body, headers={"user-agent": "openai-python/1.99"}),
    )

    assert real == stubbed
    status, _response_headers, content = real
    assert status == 200
    assert json.loads(content)["id"] == "resp_golden_json"
    assert len(relayed) == 2


# -- structural half: nothing is probed, looked up, selected, walked or claimed when unconfigured -----------

# (attribute name, modules that may bind it). Patched wherever the name exists so both ``from x import y``
# and ``x.y()`` call styles are intercepted.
_NEVER_CALLED: tuple[tuple[str, tuple[Any, ...]], ...] = (
    ("probe_pool_usage_exhaustion", (probe_module, overflow_module)),
    ("lookup_pin_bounded", (pins_module, overflow_module)),
    ("lookup_pins_bounded", (pins_module, overflow_module)),
    ("select_overflow_model_source", (selection_module, overflow_module)),
    ("overflow_portability_view", (projection_module, overflow_module)),
    ("try_claim_overflow", (overflow_module,)),
    ("try_claim", (admission_module,)),
)


def _forbid_configured_only_work(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counters: dict[str, int] = {name: 0 for name, _modules in _NEVER_CALLED}

    def spy_for(name: str, original: Any) -> Any:
        import inspect

        if inspect.iscoroutinefunction(original):

            async def counting_async(*args: object, **kwargs: object) -> Any:
                counters[name] += 1
                return await original(*args, **kwargs)

            return counting_async

        def counting(*args: object, **kwargs: object) -> Any:
            counters[name] += 1
            return original(*args, **kwargs)

        return counting

    for name, modules in _NEVER_CALLED:
        for module in modules:
            original = getattr(module, name, None)
            if original is None:
                continue
            monkeypatch.setattr(module, name, spy_for(name, original))
    return counters


@pytest.mark.asyncio
async def test_unconfigured_request_path_performs_no_probe_lookup_selection_walk_or_claim(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    counters = _forbid_configured_only_work(monkeypatch)
    # Exhausted first (today's 429), then a healthy account joins for the stream and non-stream answers.
    await _seed_exhausted_pool(async_client, tag="golden_spy_exhausted")
    exhausted = await async_client.post(
        CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_golden_spy_2", session_id="sess_golden_spy_2")
    )
    assert exhausted.status_code == 429

    await _import_account(async_client, "acc_golden_spy", "golden-spy@example.com")
    get_account_selection_cache().invalidate()
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_golden_spy")

    stream = await async_client.post(
        CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_golden_spy", session_id="sess_golden_spy")
    )
    non_stream = await async_client.post(
        V1_ROUTE, json={**_codex_body(), "stream": False}, headers={"user-agent": "openai-python/1.99"}
    )
    assert stream.status_code == 200
    assert non_stream.status_code == 200
    assert len(relayed) == 2

    assert counters == {name: 0 for name in counters}, counters
