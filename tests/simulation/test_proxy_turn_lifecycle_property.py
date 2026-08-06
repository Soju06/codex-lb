from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal, Protocol

import pytest

pytestmark = pytest.mark.unit

ScheduleEvent = Literal["admission_wait", "upstream_terminal", "downstream_cancel", "retry_request"]
Terminal = Literal["upstream_terminal", "downstream_cancel"]

_EVENTS: tuple[ScheduleEvent, ...] = (
    "admission_wait",
    "upstream_terminal",
    "downstream_cancel",
    "retry_request",
)
_SCHEDULE_COUNT = 200


class _BridgeTurn(Protocol):
    def handle(self, event: ScheduleEvent) -> None: ...

    def snapshot(self) -> "_TurnSnapshot": ...


@dataclass(frozen=True, slots=True)
class _TurnSnapshot:
    terminal_outcomes: tuple[Terminal, ...]
    response_create_releases: int
    api_key_releases: int
    account_releases: int
    retry_terminal_events: tuple[Terminal, ...]


@dataclass(slots=True)
class _DeterministicBridgeTurn:
    terminal_outcomes: list[Terminal] = field(default_factory=list)
    retry_terminal_events: list[Terminal] = field(default_factory=list)
    response_create_releases: int = 0
    api_key_releases: int = 0
    account_releases: int = 0
    _admitted: bool = False
    _retry_attached: bool = False
    _released: bool = False

    def handle(self, event: ScheduleEvent) -> None:
        if event == "admission_wait":
            self._admitted = True
            return
        if event == "retry_request":
            self._retry_attached = True
            return
        if event == "downstream_cancel":
            self._settle("downstream_cancel")
            return
        if event == "upstream_terminal":
            if self.terminal_outcomes:
                return
            self._settle("upstream_terminal")
            return

    def _settle(self, terminal: Terminal) -> None:
        if self.terminal_outcomes:
            return
        self.terminal_outcomes.append(terminal)
        self._release_once()

    def _release_once(self) -> None:
        if self._released:
            return
        self._released = True
        self.response_create_releases += 1
        self.api_key_releases += 1
        self.account_releases += 1

    def snapshot(self) -> _TurnSnapshot:
        return _TurnSnapshot(
            terminal_outcomes=tuple(self.terminal_outcomes),
            response_create_releases=self.response_create_releases,
            api_key_releases=self.api_key_releases,
            account_releases=self.account_releases,
            retry_terminal_events=tuple(self.retry_terminal_events),
        )


@dataclass(slots=True)
class _DoubleReleaseOnCancelBridgeTurn(_DeterministicBridgeTurn):
    def handle(self, event: ScheduleEvent) -> None:
        if event == "upstream_terminal" and self.terminal_outcomes == ["downstream_cancel"]:
            self.response_create_releases += 1
            self.api_key_releases += 1
            self.account_releases += 1
            if self._retry_attached:
                self.retry_terminal_events.append("upstream_terminal")
            return
        super().handle(event)


def _schedule_for_seed(seed: int) -> tuple[ScheduleEvent, ...]:
    rng = random.Random(seed)
    events = list(_EVENTS)
    rng.shuffle(events)
    if rng.random() < 0.5:
        events.insert(rng.randrange(len(events) + 1), rng.choice(_EVENTS))
    return tuple(events)


def _assert_bridge_turn_invariants(turn: _BridgeTurn, *, seed: int, schedule: tuple[ScheduleEvent, ...]) -> None:
    snapshot = turn.snapshot()
    context = f"seed={seed} schedule={schedule} snapshot={snapshot}"
    assert len(snapshot.terminal_outcomes) == 1, context
    assert snapshot.response_create_releases == 1, context
    assert snapshot.api_key_releases == 1, context
    assert snapshot.account_releases == 1, context
    assert not snapshot.retry_terminal_events, context


def _check_schedules(turn_factory: type[_BridgeTurn], *, schedule_count: int = _SCHEDULE_COUNT) -> None:
    for seed in range(schedule_count):
        schedule = _schedule_for_seed(seed)
        turn = turn_factory()
        for event in schedule:
            turn.handle(event)
        _assert_bridge_turn_invariants(turn, seed=seed, schedule=schedule)


def test_bridge_turn_lifecycle_seeded_schedules_settle_exactly_once() -> None:
    _check_schedules(_DeterministicBridgeTurn)


def test_bridge_turn_lifecycle_checker_catches_double_release_canary() -> None:
    with pytest.raises(AssertionError, match="response_create_releases"):
        _check_schedules(_DoubleReleaseOnCancelBridgeTurn)
