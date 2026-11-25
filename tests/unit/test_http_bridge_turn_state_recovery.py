from __future__ import annotations

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_turn_state_anchor_for_owner_failure


@pytest.mark.parametrize(
    ("phase", "detail", "code", "eligible"),
    [
        ("owner_forward", "owner_input_shape_upgrade_required", "bridge_owner_forward_failed", True),
        (None, "owner_input_shape_upgrade_required", "bridge_owner_forward_failed", False),
        ("owner_forward", "other", "bridge_owner_forward_failed", False),
        (None, None, "bridge_owner_unreachable", True),
        (None, None, "bridge_owner_forward_failed", False),
    ],
)
def test_turn_state_recovery_requires_unreachable_owner_or_typed_shape_failure(
    phase: str | None, detail: str | None, code: str, eligible: bool
) -> None:
    error = ProxyResponseError(
        503,
        {"error": {"code": code}},
        failure_phase=phase,
        failure_detail=detail,
    )
    anchor = _http_bridge_turn_state_anchor_for_owner_failure(
        error, headers={"X-Codex-Turn-State": "turn-state"}, previous_response_id=None
    )
    assert anchor == ("turn-state" if eligible else None)


@pytest.mark.parametrize(("turn_state", "previous_response_id"), [(None, None), ("turn-state", "resp-client")])
def test_shape_failure_does_not_replace_missing_turn_state_or_explicit_response(
    turn_state: str | None, previous_response_id: str | None
) -> None:
    error = ProxyResponseError(
        503,
        {"error": {"code": "bridge_owner_forward_failed"}},
        failure_phase="owner_forward",
        failure_detail="owner_input_shape_upgrade_required",
    )
    assert (
        _http_bridge_turn_state_anchor_for_owner_failure(
            error,
            headers={"x-codex-turn-state": turn_state} if turn_state is not None else {},
            previous_response_id=previous_response_id,
        )
        is None
    )
