from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_OWNERSHIP = ROOT / "spec" / "CoreOwnership.tla"
CORE_OWNERSHIP_CFG = ROOT / "spec" / "CoreOwnership.cfg"


def test_core_ownership_models_distinct_upstream_phases() -> None:
    spec = CORE_OWNERSHIP.read_text()

    assert 'AttemptPhases == {"none", "connect", "awaiting_first_byte", "awaiting_response", "streaming"}' in spec
    assert "UpstreamConnected(t) ==" in spec
    assert "UpstreamFirstByte(t) ==" in spec
    assert "StreamProgress(t) ==" in spec
    assert 'CASE attemptPhase[t] = "connect" -> connectDeadline[t]' in spec
    assert '[] attemptPhase[t] = "awaiting_first_byte" -> firstByteDeadline[t]' in spec


def test_core_ownership_guards_snapshot_refresh_and_retry_outcomes() -> None:
    spec = CORE_OWNERSHIP.read_text()

    assert "/\\ snapshotVersion[r][a] # durableVersion[a]" in spec
    assert "/\\ ~snapshotRoute[t].attempted" in spec
    # Acquisition consumes the route the freshness check produced, so a
    # dispatch cannot skip routing or land on an unchecked replica/account.
    assert "/\\ snapshotRoute[t].attempted" in spec
    assert "/\\ snapshotRoute[t].replica = r" in spec
    assert "/\\ snapshotRoute[t].account = a" in spec
    assert "/\\ (snapshotRoute[t].version = durableVersion[a] \\/ WeakStaleRouteAcquire)" in spec
    assert "version |-> snapshotVersion[r][a]" in spec
    assert (ROOT / "spec" / "weak-stale-route-acquire.cfg").exists()
    assert "ClientRetryFails ==" in spec
    assert "ClientRetrySucceeds ==" in spec
    assert "CompletedProducerEventuallyDelivered ==" in spec
    assert "DeliverProducer(t, u) ==" in spec
    assert "IF WeakMisrouteProducer" in spec
    assert (ROOT / "spec" / "weak-misroute-producer.cfg").exists()


def test_core_ownership_anchor_checks_are_not_vacuous() -> None:
    spec = CORE_OWNERSHIP.read_text()

    # A mismatched-lineage anchor is reachable only under its weakening, so the
    # full model rejects it before dispatch and the control remains non-vacuous.
    assert "MismatchedLineageAnchor(a) ==" in spec
    assert "lineageOk |-> FALSE" in spec
    assert "MismatchedLineage == " in spec
    assert "(inj = MismatchedLineage /\\ WeakIgnoreAnchorLineage)" in spec
    assert "/\\ badAnchorUse' = (badAnchorUse \\/ inj = MismatchedLineage)" in spec
    # One replay per anchor value: without this UseAnchor is an unconditional
    # self-loop that hides deadlocks from TLC.
    assert "/\\ ~anchorUsed[t]" in spec
    assert "WeakIgnoreAnchorLineage == " in spec
    assert (ROOT / "spec" / "weak-ignore-anchor-lineage.cfg").exists()


def test_core_ownership_live_turn_matches_durable_owner() -> None:
    spec = CORE_OWNERSHIP.read_text()

    assert "Inv5SingleOwnerCAS ==" in spec
    assert "/\\ owner[a] = turnReplica[t]" in spec
    assert "/\\ ownerEpoch[a] = turnEpoch[t]" in spec


def test_core_ownership_full_cfg_checks_completed_delivery_liveness() -> None:
    cfg = CORE_OWNERSHIP_CFG.read_text()

    assert "CompletedProducerEventuallyDelivered" in cfg


def test_core_ownership_only_completes_after_response_phase() -> None:
    spec = CORE_OWNERSHIP.read_text()

    assert "CanComplete(t) ==" in spec
    assert '/\\ (turnState[t] = "streaming" \\/ attemptPhase[t] = "awaiting_response")' in spec
    assert "CompleteTurn(t) ==" in spec
    assert "/\\ CanComplete(t)" in spec
    assert "ClaimCompletedDelivery(t) ==" in spec


def test_core_ownership_clamps_request_budget_before_each_phase_reset() -> None:
    spec = CORE_OWNERSHIP.read_text()

    assert spec.count("LET remainingRequest == SubtractFloor(requestDeadline[t], phaseElapsed[t])") == 4
    assert spec.count("/\\ requestDeadline' = [requestDeadline EXCEPT ![t] = remainingRequest]") == 4
    assert "StartStream(t, k) ==" in spec
    assert "StreamProgress(t) ==" in spec


def test_core_ownership_shutdown_waits_for_terminal_delivery() -> None:
    spec = CORE_OWNERSHIP.read_text()

    delivery_guard = 'terminalReason[t] = "completed" => producerDelivered[t]'
    assert spec.count(delivery_guard) >= 2
    assert "CompleteShutdown ==" in spec
    assert "Inv8ShutdownDrain ==" in spec


def test_tlc_metadata_stays_under_ignored_spec_state() -> None:
    checker = (ROOT / "spec" / "check.sh").read_text()

    assert 'ACTIVE_TLC_METADIR="$(mktemp -d "$SPEC_DIR/states/run.XXXXXX")"' in checker
    assert '-metadir "$ACTIVE_TLC_METADIR"' in checker
    assert "trap cleanup_tlc_metadir EXIT" in checker
