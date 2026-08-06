from __future__ import annotations

import argparse
import json
import logging
import math
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.shutdown import DRAIN_DEADLINE_HEADER

logger = logging.getLogger(__name__)

PRESTOP_REQUEST_TIMEOUT_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class DrainStatus:
    draining: bool
    in_flight: int

    def __post_init__(self) -> None:
        if self.in_flight < 0:
            raise ValueError("drain status in_flight must be non-negative")


class DrainClient(Protocol):
    def start_drain(
        self,
        *,
        deadline_monotonic: float,
        timeout_seconds: float,
    ) -> float: ...

    def get_status(self, *, timeout_seconds: float) -> DrainStatus: ...


class LocalDrainClient:
    def __init__(
        self,
        base_url: str,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._urlopen = urlopen

    def start_drain(
        self,
        *,
        deadline_monotonic: float,
        timeout_seconds: float,
    ) -> float:
        request = urllib.request.Request(
            f"{self._base_url}/internal/drain/start",
            headers={DRAIN_DEADLINE_HEADER: format(deadline_monotonic, ".17g")},
            method="POST",
        )
        with self._urlopen(request, timeout=timeout_seconds) as response:
            raw_payload = response.read()
        try:
            payload = json.loads(raw_payload or b"{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("drain start response is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("drain start response must be an ok object")
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            raise ValueError("drain start response is missing checks")
        if checks.get("draining") != "true":
            raise ValueError("drain start response has invalid draining state")
        if checks.get("shutdown_committed") != "true":
            raise ValueError("drain start response is not committed")
        deadline_value = checks.get("deadline_monotonic")
        if not isinstance(deadline_value, str):
            raise ValueError("drain start response has invalid deadline")
        try:
            effective_deadline = float(deadline_value)
        except ValueError as exc:
            raise ValueError("drain start response has invalid deadline") from exc
        if not math.isfinite(effective_deadline):
            raise ValueError("drain start response has invalid deadline")
        return effective_deadline

    def get_status(self, *, timeout_seconds: float) -> DrainStatus:
        with self._urlopen(
            f"{self._base_url}/internal/drain/status",
            timeout=timeout_seconds,
        ) as response:
            raw_payload = response.read()
        try:
            payload = json.loads(raw_payload or b"{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("drain status response is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("drain status response must be an ok object")
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            raise ValueError("drain status response is missing checks")
        draining_value = checks.get("draining")
        if draining_value not in {"true", "false"}:
            raise ValueError("drain status response has invalid draining state")
        in_flight_value = checks.get("in_flight")
        if not isinstance(in_flight_value, str):
            raise ValueError("drain status response has invalid in_flight state")
        try:
            in_flight = int(in_flight_value)
        except ValueError as exc:
            raise ValueError("drain status response has invalid in_flight state") from exc
        return DrainStatus(
            draining=draining_value == "true",
            in_flight=in_flight,
        )


def run_prestop(
    *,
    client: DrainClient,
    routing_dwell_seconds: float,
    drain_timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
) -> bool:
    """Start drain and wait for dwell plus in-flight completion on one deadline."""

    routing_dwell_seconds = float(routing_dwell_seconds)
    drain_timeout_seconds = float(drain_timeout_seconds)
    poll_interval_seconds = float(poll_interval_seconds)
    if (
        not math.isfinite(routing_dwell_seconds)
        or routing_dwell_seconds < 0
        or not math.isfinite(drain_timeout_seconds)
        or drain_timeout_seconds <= 0
        or routing_dwell_seconds > drain_timeout_seconds
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
    ):
        raise ValueError("preStop requires 0 <= routing dwell <= positive drain timeout and a positive poll interval")

    started_at = monotonic()
    deadline = started_at + drain_timeout_seconds
    remaining = deadline - monotonic()
    if remaining <= 0:
        return False
    effective_deadline = client.start_drain(
        deadline_monotonic=deadline,
        timeout_seconds=min(PRESTOP_REQUEST_TIMEOUT_SECONDS, remaining),
    )
    deadline = min(deadline, effective_deadline)

    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False

        status = client.get_status(timeout_seconds=min(PRESTOP_REQUEST_TIMEOUT_SECONDS, remaining))
        if not status.draining:
            raise ValueError("drain status reports that the process is not draining")

        now = monotonic()
        if now >= deadline:
            return False
        if now - started_at >= routing_dwell_seconds and status.in_flight == 0:
            return True

        remaining = deadline - now
        if remaining <= 0:
            return False
        sleep(min(poll_interval_seconds, remaining))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coordinate codex-lb preStop drain.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--routing-dwell-seconds", required=True, type=float)
    parser.add_argument("--drain-timeout-seconds", required=True, type=float)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        drained = run_prestop(
            client=LocalDrainClient(args.base_url),
            routing_dwell_seconds=args.routing_dwell_seconds,
            drain_timeout_seconds=args.drain_timeout_seconds,
        )
    except Exception as exc:
        logger.error("preStop drain coordination failed: %s", type(exc).__name__)
        return 1
    if not drained:
        logger.warning("preStop drain deadline reached with active work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
