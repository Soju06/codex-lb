"""Drift guards between the overflow spec deltas / operator docs and the shared interface contract (#2123 WP-C2, WP-D).

``openspec validate --strict`` checks structure, not names. The normative
deltas quote the closed enums, error codes, hint texts and request-log labels
that ``app/modules/proxy/overflow.py`` defines; these tests fail when either
side drifts, the way ``tests/unit/test_metrics.py`` does for the
``codex_lb_model_source_*`` metric names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from app.modules.proxy import overflow

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGE = REPO_ROOT / "openspec/changes/add-subscription-overflow-model-source"
_ROUTING_DELTA = _CHANGE / "specs/model-source-routing/spec.md"
_COMPAT_DELTA = _CHANGE / "specs/responses-api-compat/spec.md"
_OBSERVABILITY_DELTA = _CHANGE / "specs/proxy-runtime-observability/spec.md"
_ACCOUNT_ROUTING_DELTA = _CHANGE / "specs/account-routing/spec.md"
_TASKS = _CHANGE / "tasks.md"
_ROUTING_DOC = REPO_ROOT / "docs/routing.md"
_FRONTEND_SRC = REPO_ROOT / "frontend/src"
_LOCALES = _FRONTEND_SRC / "i18n/locales"
_SETTINGS_COMPONENT = _FRONTEND_SRC / "features/settings/components/subscription-overflow-settings.tsx"

_BACKTICKED = re.compile(r"`([^`\n]+)`")
# Every value of the closed ``outcome`` enum has one of these shapes; the
# observability delta may not quote any such token that the enum lacks.
_OUTCOME_SHAPE = re.compile(r"^(dispatched|bounced|declined|pinned|pin_commit|decision)_[a-z_]+$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backticked(path: Path) -> set[str]:
    return set(_BACKTICKED.findall(_read(path)))


def test_observability_delta_names_exactly_the_closed_outcome_enum() -> None:
    quoted = {token for token in _backticked(_OBSERVABILITY_DELTA) if _OUTCOME_SHAPE.match(token)}

    assert quoted == set(overflow.OVERFLOW_OUTCOMES), {
        "in_spec_only": sorted(quoted - set(overflow.OVERFLOW_OUTCOMES)),
        "in_code_only": sorted(set(overflow.OVERFLOW_OUTCOMES) - quoted),
    }


def test_declined_outcomes_cover_every_decline_reason() -> None:
    reasons = set(get_args(overflow.DeclineReason))

    assert {f"declined_{reason}" for reason in reasons} <= set(overflow.OVERFLOW_OUTCOMES)
    # The routing delta spells every decline reason by name, bare or as its counter outcome.
    routing_tokens = _backticked(_ROUTING_DELTA)
    missing = sorted(
        reason for reason in reasons if reason not in routing_tokens and f"declined_{reason}" not in routing_tokens
    )
    assert missing == [], missing


def test_observability_delta_names_the_routes_and_metrics() -> None:
    quoted = _backticked(_OBSERVABILITY_DELTA)
    text = _read(_OBSERVABILITY_DELTA)

    for route in (
        overflow.ROUTE_CODEX_RESPONSES,
        overflow.ROUTE_V1_RESPONSES,
        overflow.ROUTE_WEBSOCKET_HANDSHAKE,
        overflow.ROUTE_WEBSOCKET,
        overflow.ROUTE_COMPACT,
    ):
        assert route in quoted, route
    assert overflow.OVERFLOW_TOTAL_METRIC in text
    assert overflow.BREAKER_STATE_METRIC in text
    for kind in (overflow.DISPATCH_KIND_FRESH, overflow.DISPATCH_KIND_PINNED, overflow.DISPATCH_KIND_ANCHOR):
        assert kind in quoted, kind
    assert overflow.REQUEST_LOG_SOURCE_FRESH in quoted
    assert overflow.REQUEST_LOG_SOURCE_PINNED in quoted
    # The design's separate overflow result counter was folded into the dispatch
    # counter; naming it would also trip the model-source metric drift guard's
    # successor regex once it covers the overflow prefix.
    assert "codex_lb_subscription_overflow_source_result_total" not in text


@pytest.mark.parametrize(
    "code",
    [
        overflow.PIN_UNAVAILABLE_CODE,
        overflow.SOURCE_UNAVAILABLE_CODE,
        overflow.UNSUPPORTED_INPUT_CODE,
        overflow.HANDSHAKE_DENIAL_CODE,
        overflow.WS_BOUNCE_CODE,
        overflow.MODEL_SOURCE_UNAVAILABLE_CODE,
        overflow.MODEL_SOURCE_BUSY_CODE,
    ],
)
def test_compat_delta_quotes_every_overflow_error_code(code: str) -> None:
    assert code in _backticked(_COMPAT_DELTA), code


def test_routing_delta_quotes_the_row_and_pin_failure_codes() -> None:
    quoted = _backticked(_ROUTING_DELTA)

    for token in (
        overflow.PIN_UNAVAILABLE_CODE,
        overflow.PIN_UNVERIFIED_CODE,
        overflow.SOURCE_UNAVAILABLE_CODE,
        overflow.UNSUPPORTED_INPUT_CODE,
        overflow.MODEL_SOURCE_UNAVAILABLE_CODE,
        overflow.MODEL_SOURCE_BUSY_CODE,
        overflow.REQUEST_LOG_SOURCE_FRESH,
        overflow.REQUEST_LOG_SOURCE_PINNED,
        overflow.SUBAGENT_HEADER,
        overflow.MEMGEN_HEADER,
        *sorted(overflow.BACKGROUND_ALLOWLIST),
    ):
        assert token in quoted, token
    assert f"Retry-After: {overflow.RETRY_AFTER_SECONDS}" in quoted


def test_hint_texts_are_quoted_verbatim_in_spec_and_docs() -> None:
    compat = _read(_COMPAT_DELTA)
    docs = _read(_ROUTING_DOC)

    assert f"`{overflow.HINT_HEADER}: {overflow.HINT_NATIVE_TEXT}`" in compat
    assert overflow.HINT_SDK_SENTENCE in compat
    assert overflow.HINT_HEADER in docs
    assert overflow.HINT_NATIVE_TEXT in docs


def test_forbidden_bounce_codes_appear_only_as_prohibitions() -> None:
    compat = _read(_COMPAT_DELTA)

    for forbidden in ("server_is_overloaded", "slow_down"):
        assert f"`{forbidden}`" in compat, forbidden
        for match in re.finditer(re.escape(f"`{forbidden}`"), compat):
            window = compat[max(0, match.start() - 200) : match.start()].lower()
            assert "never" in window or "not" in window, compat[max(0, match.start() - 120) : match.end()]


def test_account_routing_delta_states_the_trigger_and_the_drain_reversal() -> None:
    text = _read(_ACCOUNT_ROUTING_DELTA)

    assert "The overflow trigger is the probe's answer" in text
    assert "never" in text and "single_account" in text
    assert "no second settings read" in text


def test_tasks_mark_the_routing_extension_done_and_list_the_wiring_packages() -> None:
    text = _read(_TASKS)

    assert "- [x] 3.1 " in text
    for task in ("3.22", "3.23", "3.24", "3.25", "3.26", "3.27", "3.28"):
        assert f" {task} " in text, task


def test_routing_doc_states_shipped_status_canary_and_client_floor() -> None:
    docs = _read(_ROUTING_DOC)

    assert "Shipping in stages" not in docs
    assert "lands in a later release" not in docs
    assert "### Canary and drills" in docs
    for drill in ("Disconnect", "Stall", "Silent headers", "Neutral release", "Clear-then-touch", "Kill switches"):
        assert f"| {drill} |" in docs, drill
    assert "0.99.0" in docs
    assert "own database" in docs
    assert "no per-replica flag" in docs
    assert overflow.OVERFLOW_TOTAL_METRIC in docs
    assert overflow.BREAKER_STATE_METRIC in docs


def test_dashboard_staged_notice_is_gone_from_the_component_and_every_locale() -> None:
    assert "stagedNotice" not in _read(_SETTINGS_COMPONENT)

    locales = {path.name: json.loads(_read(path)) for path in sorted(_LOCALES.glob("*.json"))}
    assert set(locales) == {"en.json", "ko.json", "zh-CN.json"}
    for name, resource in locales.items():
        assert "settings.routing.subscriptionOverflow.stagedNotice" not in resource, name
    overflow_keys = {
        name: {key for key in resource if key.startswith("settings.routing.subscriptionOverflow.")}
        for name, resource in locales.items()
    }
    assert overflow_keys["ko.json"] == overflow_keys["en.json"]
    assert overflow_keys["zh-CN.json"] == overflow_keys["en.json"]
