from __future__ import annotations

import pytest

from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay


@pytest.mark.parametrize("program", [{}, {"cyber": "standard"}, {"cyber": "daybreak_blue"}])
def test_native_program_preserves_account_neutral_replay(program):
    payload = {"model": "gpt-6-sol", "input": "fresh user request", "access_programs": program}
    assert responses_payload_is_account_neutral_fresh_replay(payload)
    payload["previous_response_id"] = "resp_owned"
    assert not responses_payload_is_account_neutral_fresh_replay(payload)


@pytest.mark.parametrize("program", [None, True, {"cyber": None}, {"cyber": "daybreak_red"}, {"future": "unknown"}])
def test_unvalidated_program_cannot_authorize_cross_account_replay(program):
    assert not responses_payload_is_account_neutral_fresh_replay(
        {"model": "gpt-6-sol", "input": "fresh user request", "access_programs": program}
    )
