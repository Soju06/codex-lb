"""Lazy handle onto the process-wide scheduler leader election.

Background schedulers resolve the election at call time instead of importing
:mod:`app.core.scheduling.leader_election` at module load, so importing a
scheduler never drags in the election's database session dependencies. Every
scheduler binds :func:`get_leader_election` to a module global named
``_get_leader_election`` so tests can swap in a fake leader per module.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, cast

_T = TypeVar("_T")


class LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def get_leader_election() -> LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(LeaderElectionLike, module.get_leader_election())
