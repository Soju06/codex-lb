----------------------------- MODULE CoreOwnership -----------------------------
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Replicas, Accounts, Turns, Weakening

NoReplica == "no_replica"
NoAccount == "no_account"
ForeignAccount == "foreign_account"
NoEpoch == 0
MaxClock == 3

(***************************************************************************)
(* Named timeout budgets, expressed in model ticks.  These mirror the live  *)
(* codex-lb settings pinned by the 2026-08-06 misclassification audit:      *)
(*                                                                         *)
(*   sse_keepalive_interval_seconds               -> KeepaliveInterval      *)
(*   _STREAM_KEEPALIVE_MAX_COUNT                  -> MaxKeepaliveCount      *)
(*   ..._bridge_stuck_gate_retire_after_seconds   -> GateRetireBudget       *)
(*   stream_idle_timeout_seconds                  -> StreamIdleBudget       *)
(*                                                                         *)
(* The pre-response eventless bound is derived, not free.  Live it was an   *)
(* implicit 6 * 10s = 60s that had no relation to any other budget and was  *)
(* still reported under the 7200s stream-idle name.  The derivation modeled *)
(* here is the shipped one: the pre-response bound is the minimum of the    *)
(* named owner-side gate-retire, client-safe cap, and stream-idle budgets,   *)
(* so it can never outlive the owner-side gate and can                      *)
(* never be confused with the post-start stream-idle budget.                *)
(***************************************************************************)
Min2(x, y) == IF x <= y THEN x ELSE y
DeadlineFromNow(now, b) == Min2(now + b, MaxClock)

KeepaliveInterval == 1
MaxKeepaliveCount == 2
KeepaliveCadenceFloor == KeepaliveInterval * MaxKeepaliveCount
GateRetireBudget == 2
StreamIdleBudget == 3
ClientSafePreResponseCap == KeepaliveCadenceFloor
PreResponseBudget == Min2(Min2(GateRetireBudget, ClientSafePreResponseCap), StreamIdleBudget)

(***************************************************************************)
(* Client retry backoff after a recoverable tear.  The full model assumes   *)
(* bounded delay: the backoff never grows past MaxRetryBackoff, so a retry  *)
(* is always eventually due.  RetryBackoffCap only keeps the weakened state *)
(* space finite.                                                            *)
(***************************************************************************)
MaxRetryBackoff == 1
RetryBackoffCap == 2

WeakIgnoreOwnerEpoch == Weakening = "ignore_owner_epoch"
WeakSingleTimeout == Weakening = "single_shared_timeout"
WeakSkipReleaseOnCancel == Weakening = "skip_release_on_cancel"
WeakNonAtomicClaim == Weakening = "non_atomic_claim"
WeakStaleCache == Weakening = "stale_cache"
WeakLostWaiter == Weakening = "lost_waiter"
WeakShutdownAdmit == Weakening = "shutdown_admit"
WeakDoubleSettle == Weakening = "double_settle"
WeakLeakOwnerOnTerminal == Weakening = "leak_owner_on_terminal"
WeakPoppedNotFinalized == Weakening = "popped_not_finalized"
WeakCrossAccountAnchor == Weakening = "cross_account_anchor"
WeakConflatedTimers == Weakening = "conflated_timers"
WeakUnboundedBackoff == Weakening = "unbounded_backoff"

TerminalStates == {"completed", "cancelled", "failed", "retryable_owner_loss"}
NonTerminalStates == {"new", "queued", "active", "streaming", "completed_delivery_claimed"}
ReservationStates == {"none", "held", "released", "finalized", "transferred"}
GateStates == {"none", "queued", "holding", "terminal"}
AnchorKinds == {"none", "client_anchor", "proxy_full_resend_anchor", "proxy_delta_anchor"}
SafeRecoveryKinds == {"client_anchor", "proxy_full_resend_anchor"}
Reasons == {"none", "completed", "cancelled", "timeout", "owner_loss"}
AnchorOwners == Accounts \cup {NoAccount, ForeignAccount}
SameAccount == "same_account"
InjectionChoices == {NoAccount, ForeignAccount, SameAccount}
RetryStates == {"attached", "torn", "recovered"}

VARIABLES
  clock,
  owner,
  ownerEpoch,
  turnState,
  turnReplica,
  turnAccount,
  turnEpoch,
  acquisitionCount,
  settlementCount,
  reservation,
  gate,
  gateDeadline,
  requestDeadline,
  connectDeadline,
  firstByteDeadline,
  preResponseDeadline,
  gateRetireDeadline,
  mislabeledKill,
  anchor,
  anchorUsed,
  badAnchorUse,
  crossAccountDispatch,
  durableVersion,
  snapshotVersion,
  routedWithStaleSnapshot,
  snapshotRouteAttempted,
  producerTarget,
  terminalReason,
  shutdownPhase,
  registered,
  ownerReleased,
  poppedFromPending,
  completedDeliveryClaimed,
  finalizerOwner,
  finalizerAborted,
  admittedDuringDrain,
  clientRetry,
  retryBackoff

vars == << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount, turnEpoch,
  acquisitionCount, settlementCount, reservation, gate, gateDeadline, requestDeadline,
  connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
  mislabeledKill, anchor, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
  snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
  terminalReason, shutdownPhase, registered, ownerReleased, poppedFromPending,
  completedDeliveryClaimed, finalizerOwner, finalizerAborted, admittedDuringDrain,
  clientRetry, retryBackoff >>

Inc(n) == IF n < 2 THEN n + 1 ELSE n

ReleaseOwnerFor(t) ==
  IF turnAccount[t] \in Accounts
  THEN [owner EXCEPT ![turnAccount[t]] =
    IF ownerEpoch[turnAccount[t]] = turnEpoch[t] THEN NoReplica ELSE @]
  ELSE owner

Init ==
  /\ clock = 0
  /\ owner = [a \in Accounts |-> NoReplica]
  /\ ownerEpoch = [a \in Accounts |-> NoEpoch]
  /\ turnState = [t \in Turns |-> "new"]
  /\ turnReplica = [t \in Turns |-> NoReplica]
  /\ turnAccount = [t \in Turns |-> NoAccount]
  /\ turnEpoch = [t \in Turns |-> NoEpoch]
  /\ acquisitionCount = [t \in Turns |-> 0]
  /\ settlementCount = [t \in Turns |-> 0]
  /\ reservation = [t \in Turns |-> "none"]
  /\ gate = [t \in Turns |-> "none"]
  /\ gateDeadline = [t \in Turns |-> 0]
  /\ requestDeadline = [t \in Turns |-> 0]
  /\ connectDeadline = [t \in Turns |-> 0]
  /\ firstByteDeadline = [t \in Turns |-> 0]
  /\ preResponseDeadline = [t \in Turns |-> 0]
  /\ gateRetireDeadline = [t \in Turns |-> 0]
  /\ mislabeledKill = FALSE
  /\ anchor = [t \in Turns |->
      [kind |-> "none", account |-> NoAccount, epoch |-> NoEpoch, lineageOk |-> TRUE]]
  /\ anchorUsed = [t \in Turns |-> FALSE]
  /\ badAnchorUse = FALSE
  /\ crossAccountDispatch = FALSE
  /\ durableVersion = [a \in Accounts |-> 0]
  /\ snapshotVersion = [r \in Replicas |-> [a \in Accounts |-> 0]]
  /\ routedWithStaleSnapshot = [t \in Turns |-> FALSE]
  /\ snapshotRouteAttempted = [t \in Turns |-> FALSE]
  /\ producerTarget = [t \in Turns |-> t]
  /\ terminalReason = [t \in Turns |-> "none"]
  /\ shutdownPhase = "running"
  /\ registered = [t \in Turns |-> FALSE]
  /\ ownerReleased = [t \in Turns |-> FALSE]
  /\ poppedFromPending = [t \in Turns |-> FALSE]
  /\ completedDeliveryClaimed = [t \in Turns |-> FALSE]
  /\ finalizerOwner = [t \in Turns |-> NoReplica]
  /\ finalizerAborted = [t \in Turns |-> FALSE]
  /\ admittedDuringDrain = FALSE
  /\ clientRetry = "attached"
  /\ retryBackoff = 0

LiveOnAccount(a) ==
  {t \in Turns : turnState[t] \in {"active", "streaming", "completed_delivery_claimed"} /\ turnAccount[t] = a}

CanAdmit ==
  (shutdownPhase = "running" \/ WeakShutdownAdmit)

(***************************************************************************)
(* Anchor account ownership.  A continuity anchor carries the account that  *)
(* owns it.  Upstream accepts a request that injects a previous_response_id *)
(* owned by another account, but it then never emits response.created, so   *)
(* the turn sits in the pre-response eventless phase until a timer kills    *)
(* it.  UpstreamRespondsTo is exactly that: upstream only produces the      *)
(* created/streaming/completed events for a same-account anchor.            *)
(***************************************************************************)
UpstreamRespondsTo(t) ==
  \/ anchor[t].kind = "none"
  \/ anchor[t].account = turnAccount[t]

(***************************************************************************)
(* A dispatch may inject a durable continuity anchor owned by another       *)
(* account.  Like MisrouteProducer, the injection itself is only reachable  *)
(* under its weakening: the full model refuses it at AcquireTurn.  Anchors  *)
(* owned by the serving account are produced by StartStream and shown       *)
(* usable by UseAnchor, so the guard is not vacuous.                        *)
(***************************************************************************)
ForeignAnchor ==
  [kind |-> "client_anchor",
   account |-> ForeignAccount,
   epoch |-> NoEpoch,
   lineageOk |-> TRUE]

(***************************************************************************)
(* Two distinct eventless timers.  The pre-response phase (turnState        *)
(* "active": request dispatched upstream, no response.created yet) is       *)
(* bounded by preResponseDeadline.  The post-start phase (turnState         *)
(* "streaming") is bounded by the request/stream-idle budget.  Conflating   *)
(* them means a pre-start kill is reported under the post-start budget.     *)
(***************************************************************************)
ExpireBoundFor(t) ==
  CASE turnState[t] = "queued" -> gateDeadline[t]
    [] turnState[t] = "active" -> preResponseDeadline[t]
    [] OTHER -> requestDeadline[t]

KillBudgetFor(t) ==
  CASE turnState[t] = "queued" -> "gate"
    [] turnState[t] = "active" ->
         IF WeakConflatedTimers THEN "stream_idle" ELSE "pre_response_eventless"
    [] OTHER -> "stream_idle"

Tick ==
  /\ clock < MaxClock
  /\ clock' = clock + 1
  /\ UNCHANGED << owner, ownerEpoch, turnState, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, settlementCount, reservation, gate, gateDeadline, requestDeadline,
    connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
    mislabeledKill, anchor, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    terminalReason, shutdownPhase, registered, ownerReleased, poppedFromPending,
    completedDeliveryClaimed, finalizerOwner, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

QueueTurn(t) ==
  /\ clock = 0
  /\ turnState[t] = "new"
  /\ CanAdmit
  /\ turnState' = [turnState EXCEPT ![t] = "queued"]
  /\ gate' = [gate EXCEPT ![t] = "queued"]
  /\ gateDeadline' = [gateDeadline EXCEPT ![t] = IF WeakSingleTimeout THEN 4 ELSE 3]
  /\ requestDeadline' = [requestDeadline EXCEPT ![t] = gateDeadline'[t]]
  /\ connectDeadline' = [connectDeadline EXCEPT ![t] = gateDeadline'[t]]
  /\ firstByteDeadline' = [firstByteDeadline EXCEPT ![t] = gateDeadline'[t]]
  /\ preResponseDeadline' = [preResponseDeadline EXCEPT ![t] = gateDeadline'[t]]
  /\ gateRetireDeadline' = [gateRetireDeadline EXCEPT ![t] = gateDeadline'[t]]
  /\ registered' = [registered EXCEPT ![t] = TRUE]
  /\ admittedDuringDrain' = (admittedDuringDrain \/ shutdownPhase # "running")
  /\ UNCHANGED << clock, owner, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, settlementCount, reservation, mislabeledKill, anchor, anchorUsed,
    badAnchorUse, crossAccountDispatch, durableVersion, snapshotVersion,
    routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget, terminalReason,
    shutdownPhase, ownerReleased, poppedFromPending, completedDeliveryClaimed,
    finalizerOwner, finalizerAborted, clientRetry, retryBackoff >>

AcquireTurn(t, r, a, inj) ==
  /\ turnState[t] = "queued"
  /\ r \in Replicas
  /\ a \in Accounts
  /\ inj \in InjectionChoices
  /\ (owner[a] = NoReplica \/ WeakNonAtomicClaim)
  /\ (WeakNonAtomicClaim \/ LiveOnAccount(a) = {})
  /\ (inj = NoAccount \/ inj = SameAccount \/ WeakCrossAccountAnchor)
  /\ crossAccountDispatch' = (crossAccountDispatch \/ inj = ForeignAccount)
  /\ anchor' = [anchor EXCEPT ![t] =
      CASE inj = NoAccount -> @
        [] inj = SameAccount -> [kind |-> "client_anchor", account |-> a,
                                  epoch |-> ownerEpoch[a] + 1, lineageOk |-> TRUE]
        [] OTHER -> ForeignAnchor]
  /\ owner' = [owner EXCEPT ![a] = r]
  /\ ownerEpoch' = [ownerEpoch EXCEPT ![a] = @ + 1]
  /\ turnState' = [turnState EXCEPT ![t] = "active"]
  /\ turnReplica' = [turnReplica EXCEPT ![t] = r]
  /\ turnAccount' = [turnAccount EXCEPT ![t] = a]
  /\ turnEpoch' = [turnEpoch EXCEPT ![t] = ownerEpoch[a] + 1]
  /\ acquisitionCount' = [acquisitionCount EXCEPT ![t] = @ + 1]
  /\ reservation' = [reservation EXCEPT ![t] = "held"]
  /\ gate' = [gate EXCEPT ![t] = "holding"]
  /\ gateDeadline' = [gateDeadline EXCEPT ![t] = DeadlineFromNow(clock, GateRetireBudget)]
  /\ requestDeadline' = [requestDeadline EXCEPT ![t] = DeadlineFromNow(clock, StreamIdleBudget)]
  /\ connectDeadline' = [connectDeadline EXCEPT ![t] = DeadlineFromNow(clock, IF WeakSingleTimeout THEN 3 ELSE 1)]
  /\ firstByteDeadline' = [firstByteDeadline EXCEPT ![t] = DeadlineFromNow(clock, 2)]
  /\ preResponseDeadline' = [preResponseDeadline EXCEPT ![t] = DeadlineFromNow(clock, PreResponseBudget)]
  /\ gateRetireDeadline' = [gateRetireDeadline EXCEPT ![t] = DeadlineFromNow(clock, GateRetireBudget)]
  /\ clientRetry' = "attached"
  /\ UNCHANGED << clock, settlementCount, mislabeledKill, anchorUsed,
    badAnchorUse, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, terminalReason, shutdownPhase, registered,
    ownerReleased, poppedFromPending, completedDeliveryClaimed, finalizerOwner,
    finalizerAborted, admittedDuringDrain, retryBackoff >>

StartStream(t, k) ==
  /\ turnState[t] = "active"
  /\ UpstreamRespondsTo(t)
  /\ k \in AnchorKinds \ {"none"}
  /\ turnState' = [turnState EXCEPT ![t] = "streaming"]
  /\ anchor' = [anchor EXCEPT ![t] =
      [kind |-> k, account |-> turnAccount[t], epoch |-> turnEpoch[t], lineageOk |-> TRUE]]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
    mislabeledKill, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    terminalReason, shutdownPhase, registered, ownerReleased, poppedFromPending,
    completedDeliveryClaimed, finalizerOwner, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

AnchorSafe(t) ==
  /\ anchor[t].kind \in SafeRecoveryKinds
  /\ anchor[t].lineageOk
  /\ anchor[t].account \in Accounts
  /\ anchor[t].epoch = ownerEpoch[anchor[t].account]
  /\ owner[anchor[t].account] # NoReplica

UseAnchor(t) ==
  /\ turnState[t] \in {"active", "streaming"}
  /\ anchor[t].kind # "none"
  /\ (AnchorSafe(t) \/ WeakIgnoreOwnerEpoch)
  /\ anchorUsed' = [anchorUsed EXCEPT ![t] = TRUE]
  /\ badAnchorUse' = (badAnchorUse \/ ~AnchorSafe(t))
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    terminalReason, shutdownPhase, registered, ownerReleased, poppedFromPending,
    completedDeliveryClaimed, finalizerOwner, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

OwnerLoss(t) ==
  /\ turnState[t] \in {"active", "streaming", "completed_delivery_claimed"}
  /\ turnAccount[t] \in Accounts
  /\ owner' = [owner EXCEPT ![turnAccount[t]] = NoReplica]
  /\ ownerEpoch' = [ownerEpoch EXCEPT ![turnAccount[t]] = @ + 1]
  /\ IF WeakIgnoreOwnerEpoch
     THEN
       /\ UNCHANGED << turnState, settlementCount, reservation, gate, registered,
         terminalReason, ownerReleased, finalizerOwner >>
     ELSE
       /\ turnState' = [turnState EXCEPT ![t] = "retryable_owner_loss"]
       /\ settlementCount' = [settlementCount EXCEPT ![t] = Inc(@)]
       /\ reservation' = [reservation EXCEPT ![t] = "released"]
       /\ gate' = [gate EXCEPT ![t] = "terminal"]
       /\ registered' = [registered EXCEPT ![t] = FALSE]
       /\ terminalReason' = [terminalReason EXCEPT ![t] = "owner_loss"]
       /\ ownerReleased' = [ownerReleased EXCEPT ![t] = TRUE]
       /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = NoReplica]
  /\ UNCHANGED << clock, turnReplica, turnAccount, turnEpoch, acquisitionCount,
    gateDeadline, requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, shutdownPhase, poppedFromPending,
    completedDeliveryClaimed, finalizerAborted, admittedDuringDrain, clientRetry,
    retryBackoff >>

CompleteTurn(t) ==
  /\ turnState[t] \in {"active", "streaming"}
  /\ UpstreamRespondsTo(t)
  /\ turnState' = [turnState EXCEPT ![t] = "completed"]
  /\ settlementCount' = [settlementCount EXCEPT ![t] = Inc(@)]
  /\ reservation' = [reservation EXCEPT ![t] = "finalized"]
  /\ gate' = [gate EXCEPT ![t] = "terminal"]
  /\ terminalReason' = [terminalReason EXCEPT ![t] = "completed"]
  /\ registered' = [registered EXCEPT ![t] = FALSE]
  /\ ownerReleased' = [ownerReleased EXCEPT ![t] = ~WeakLeakOwnerOnTerminal]
  /\ owner' = IF WeakLeakOwnerOnTerminal THEN owner ELSE ReleaseOwnerFor(t)
  /\ UNCHANGED << clock, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, gateDeadline, requestDeadline, connectDeadline, firstByteDeadline,
    preResponseDeadline, gateRetireDeadline, mislabeledKill, anchor, anchorUsed,
    badAnchorUse, crossAccountDispatch, durableVersion, snapshotVersion,
    routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget, shutdownPhase,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

ClaimCompletedDelivery(t) ==
  /\ turnState[t] \in {"active", "streaming"}
  /\ UpstreamRespondsTo(t)
  /\ turnReplica[t] \in Replicas
  /\ turnState' = [turnState EXCEPT ![t] = "completed_delivery_claimed"]
  /\ poppedFromPending' = [poppedFromPending EXCEPT ![t] = TRUE]
  /\ completedDeliveryClaimed' = [completedDeliveryClaimed EXCEPT ![t] = TRUE]
  /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = turnReplica[t]]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, settlementCount, reservation, gate, gateDeadline, requestDeadline,
    connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
    mislabeledKill, anchor, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    terminalReason, shutdownPhase, registered, ownerReleased, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

FinalizeCompletedDelivery(t) ==
  /\ turnState[t] = "completed_delivery_claimed"
  /\ finalizerOwner[t] = turnReplica[t]
  /\ turnState' = [turnState EXCEPT ![t] = "completed"]
  /\ settlementCount' = [settlementCount EXCEPT ![t] = Inc(@)]
  /\ reservation' = [reservation EXCEPT ![t] = "finalized"]
  /\ gate' = [gate EXCEPT ![t] = "terminal"]
  /\ terminalReason' = [terminalReason EXCEPT ![t] = "completed"]
  /\ registered' = [registered EXCEPT ![t] = FALSE]
  /\ ownerReleased' = [ownerReleased EXCEPT ![t] = ~WeakLeakOwnerOnTerminal]
  /\ owner' = IF WeakLeakOwnerOnTerminal THEN owner ELSE ReleaseOwnerFor(t)
  /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = NoReplica]
  /\ UNCHANGED << clock, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, gateDeadline, requestDeadline, connectDeadline, firstByteDeadline,
    preResponseDeadline, gateRetireDeadline, mislabeledKill, anchor, anchorUsed,
    badAnchorUse, crossAccountDispatch, durableVersion, snapshotVersion,
    routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget, shutdownPhase,
    poppedFromPending, completedDeliveryClaimed, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

AbortCompletedDelivery(t) ==
  /\ WeakPoppedNotFinalized
  /\ turnState[t] = "completed_delivery_claimed"
  /\ turnState' = [turnState EXCEPT ![t] = "completed"]
  /\ gate' = [gate EXCEPT ![t] = "terminal"]
  /\ terminalReason' = [terminalReason EXCEPT ![t] = "completed"]
  /\ registered' = [registered EXCEPT ![t] = FALSE]
  /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = NoReplica]
  /\ finalizerAborted' = [finalizerAborted EXCEPT ![t] = TRUE]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, settlementCount, reservation, gateDeadline, requestDeadline,
    connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
    mislabeledKill, anchor, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    shutdownPhase, ownerReleased, poppedFromPending, completedDeliveryClaimed,
    admittedDuringDrain, clientRetry, retryBackoff >>

CancelTurn(t) ==
  /\ turnState[t] \in {"queued", "active", "streaming", "completed_delivery_claimed"}
  /\ turnState' = [turnState EXCEPT ![t] = "cancelled"]
  /\ settlementCount' = [settlementCount EXCEPT ![t] =
      IF reservation[t] = "held" /\ ~WeakSkipReleaseOnCancel THEN Inc(@) ELSE @]
  /\ reservation' = [reservation EXCEPT ![t] =
      IF WeakSkipReleaseOnCancel THEN reservation[t]
      ELSE IF reservation[t] = "held" THEN "released" ELSE reservation[t]]
  /\ gate' = [gate EXCEPT ![t] =
      IF WeakLostWaiter /\ gate[t] = "queued" THEN "none" ELSE "terminal"]
  /\ terminalReason' = [terminalReason EXCEPT ![t] = "cancelled"]
  /\ registered' = [registered EXCEPT ![t] = FALSE]
  /\ ownerReleased' = [ownerReleased EXCEPT ![t] =
      IF turnAccount[t] \in Accounts THEN ~WeakLeakOwnerOnTerminal ELSE TRUE]
  /\ owner' = IF WeakLeakOwnerOnTerminal THEN owner ELSE ReleaseOwnerFor(t)
  /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = NoReplica]
  /\ UNCHANGED << clock, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, gateDeadline, requestDeadline, connectDeadline, firstByteDeadline,
    preResponseDeadline, gateRetireDeadline, mislabeledKill, anchor, anchorUsed,
    badAnchorUse, crossAccountDispatch, durableVersion, snapshotVersion,
    routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget, shutdownPhase,
    poppedFromPending, completedDeliveryClaimed, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

ExpireDeadline(t) ==
  /\ turnState[t] \in {"queued", "active", "streaming"}
  /\ clock >= ExpireBoundFor(t)
  /\ turnState' = [turnState EXCEPT ![t] = "failed"]
  /\ mislabeledKill' =
      (mislabeledKill \/ (KillBudgetFor(t) = "stream_idle" /\ turnState[t] # "streaming"))
  /\ clientRetry' =
      IF turnState[t] = "active" THEN "torn" ELSE clientRetry
  /\ settlementCount' = [settlementCount EXCEPT ![t] =
      IF reservation[t] = "held" THEN Inc(@) ELSE @]
  /\ reservation' = [reservation EXCEPT ![t] =
      IF reservation[t] = "held" THEN "released" ELSE reservation[t]]
  /\ gate' = [gate EXCEPT ![t] = "terminal"]
  /\ terminalReason' = [terminalReason EXCEPT ![t] = "timeout"]
  /\ registered' = [registered EXCEPT ![t] = FALSE]
  /\ ownerReleased' = [ownerReleased EXCEPT ![t] =
      IF turnAccount[t] \in Accounts THEN ~WeakLeakOwnerOnTerminal ELSE TRUE]
  /\ owner' = IF WeakLeakOwnerOnTerminal THEN owner ELSE ReleaseOwnerFor(t)
  /\ finalizerOwner' = [finalizerOwner EXCEPT ![t] = NoReplica]
  /\ UNCHANGED << clock, ownerEpoch, turnReplica, turnAccount, turnEpoch,
    acquisitionCount, gateDeadline, requestDeadline, connectDeadline, firstByteDeadline,
    preResponseDeadline, gateRetireDeadline, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, shutdownPhase, poppedFromPending,
    completedDeliveryClaimed, finalizerAborted, admittedDuringDrain, retryBackoff >>

(***************************************************************************)
(* Client recovery after a recoverable tear.  GrowRetryBackoff is the       *)
(* client sleeping longer between reconnects; ClientRetryAttempt is the     *)
(* reconnect that repairs the session.  The bounded-delay assumption is the *)
(* guard retryBackoff <= MaxRetryBackoff on ClientRetryAttempt: while the   *)
(* backoff stays inside its bound the retry is always eventually due.       *)
(***************************************************************************)
GrowRetryBackoff ==
  /\ clientRetry = "torn"
  /\ retryBackoff < RetryBackoffCap
  /\ (retryBackoff < MaxRetryBackoff \/ WeakUnboundedBackoff)
  /\ retryBackoff' = retryBackoff + 1
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, terminalReason, shutdownPhase, registered,
    ownerReleased, poppedFromPending, completedDeliveryClaimed, finalizerOwner,
    finalizerAborted, admittedDuringDrain, clientRetry >>

ClientRetryAttempt ==
  /\ clientRetry = "torn"
  /\ retryBackoff <= MaxRetryBackoff
  /\ clientRetry' = "recovered"
  /\ retryBackoff' = 0
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, terminalReason, shutdownPhase, registered,
    ownerReleased, poppedFromPending, completedDeliveryClaimed, finalizerOwner,
    finalizerAborted, admittedDuringDrain >>

DuplicateSettlement(t) ==
  /\ WeakDoubleSettle
  /\ acquisitionCount[t] > 0
  /\ turnState[t] \in TerminalStates
  /\ settlementCount[t] = 1
  /\ settlementCount' = [settlementCount EXCEPT ![t] = Inc(@)]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, reservation, gate, gateDeadline, requestDeadline,
    connectDeadline, firstByteDeadline, preResponseDeadline, gateRetireDeadline,
    mislabeledKill, anchor, anchorUsed, badAnchorUse, crossAccountDispatch, durableVersion,
    snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted, producerTarget,
    terminalReason, shutdownPhase, registered, ownerReleased, poppedFromPending,
    completedDeliveryClaimed, finalizerOwner, finalizerAborted, admittedDuringDrain,
    clientRetry, retryBackoff >>

InvalidateQuota(a) ==
  /\ a \in Accounts
  /\ durableVersion[a] = 0
  /\ durableVersion' = [durableVersion EXCEPT ![a] = @ + 1]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, snapshotVersion, routedWithStaleSnapshot, snapshotRouteAttempted,
    producerTarget, terminalReason, shutdownPhase, registered, ownerReleased,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

RefreshSnapshot(r, a) ==
  /\ r \in Replicas
  /\ a \in Accounts
  /\ snapshotVersion' = [snapshotVersion EXCEPT ![r][a] = durableVersion[a]]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, routedWithStaleSnapshot, snapshotRouteAttempted,
    producerTarget, terminalReason, shutdownPhase, registered, ownerReleased,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

RouteFromSnapshot(t, r, a) ==
  /\ turnState[t] = "queued"
  /\ r \in Replicas
  /\ a \in Accounts
  /\ (snapshotVersion[r][a] >= durableVersion[a] \/ WeakStaleCache)
  /\ snapshotRouteAttempted' = [snapshotRouteAttempted EXCEPT ![t] = TRUE]
  /\ routedWithStaleSnapshot' = [routedWithStaleSnapshot EXCEPT ![t] =
      snapshotVersion[r][a] < durableVersion[a]]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, producerTarget, terminalReason,
    shutdownPhase, registered, ownerReleased, poppedFromPending, completedDeliveryClaimed,
    finalizerOwner, finalizerAborted, admittedDuringDrain, clientRetry, retryBackoff >>

MisrouteProducer(t, u) ==
  /\ t \in Turns
  /\ u \in Turns
  /\ t # u
  /\ turnState[t] \in TerminalStates
  /\ turnState[u] \in {"queued", "active", "streaming"}
  /\ WeakLostWaiter
  /\ producerTarget' = [producerTarget EXCEPT ![t] = u]
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, terminalReason, shutdownPhase, registered, ownerReleased,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

StartDrain ==
  /\ shutdownPhase = "running"
  /\ shutdownPhase' = "draining"
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, terminalReason, registered, ownerReleased,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

CompleteShutdown ==
  /\ shutdownPhase = "draining"
  /\ \A t \in Turns : registered[t] = FALSE
  /\ shutdownPhase' = "complete"
  /\ UNCHANGED << clock, owner, ownerEpoch, turnState, turnReplica, turnAccount,
    turnEpoch, acquisitionCount, settlementCount, reservation, gate, gateDeadline,
    requestDeadline, connectDeadline, firstByteDeadline, preResponseDeadline,
    gateRetireDeadline, mislabeledKill, anchor, anchorUsed, badAnchorUse,
    crossAccountDispatch, durableVersion, snapshotVersion, routedWithStaleSnapshot,
    snapshotRouteAttempted, producerTarget, terminalReason, registered, ownerReleased,
    poppedFromPending, completedDeliveryClaimed, finalizerOwner, finalizerAborted,
    admittedDuringDrain, clientRetry, retryBackoff >>

Quiesce ==
  /\ shutdownPhase = "complete"
  /\ \A t \in Turns : turnState[t] \in TerminalStates \/ turnState[t] = "new"
  /\ UNCHANGED vars

Next ==
  \/ Tick
  \/ \E t \in Turns : QueueTurn(t)
  \/ \E t \in Turns, r \in Replicas, a \in Accounts, inj \in InjectionChoices :
       AcquireTurn(t, r, a, inj)
  \/ \E t \in Turns, k \in AnchorKinds \ {"none"} : StartStream(t, k)
  \/ \E t \in Turns : UseAnchor(t)
  \/ \E t \in Turns : OwnerLoss(t)
  \/ \E t \in Turns : CompleteTurn(t)
  \/ \E t \in Turns : ClaimCompletedDelivery(t)
  \/ \E t \in Turns : FinalizeCompletedDelivery(t)
  \/ \E t \in Turns : AbortCompletedDelivery(t)
  \/ \E t \in Turns : CancelTurn(t)
  \/ \E t \in Turns : ExpireDeadline(t)
  \/ \E t \in Turns : DuplicateSettlement(t)
  \/ \E a \in Accounts : InvalidateQuota(a)
  \/ \E r \in Replicas, a \in Accounts : RefreshSnapshot(r, a)
  \/ \E t \in Turns, r \in Replicas, a \in Accounts : RouteFromSnapshot(t, r, a)
  \/ \E t \in Turns, u \in Turns : MisrouteProducer(t, u)
  \/ GrowRetryBackoff
  \/ ClientRetryAttempt
  \/ StartDrain
  \/ CompleteShutdown
  \/ Quiesce

Spec ==
  /\ Init
  /\ [][Next]_vars
  /\ WF_vars(Tick)
  /\ \A t \in Turns : WF_vars(ExpireDeadline(t))
  /\ \A t \in Turns : WF_vars(FinalizeCompletedDelivery(t))
  /\ WF_vars(ClientRetryAttempt)
  /\ WF_vars(CompleteShutdown)

TypeInvariant ==
  /\ clock \in 0..MaxClock
  /\ owner \in [Accounts -> Replicas \cup {NoReplica}]
  /\ ownerEpoch \in [Accounts -> Nat]
  /\ turnState \in [Turns -> NonTerminalStates \cup TerminalStates]
  /\ turnReplica \in [Turns -> Replicas \cup {NoReplica}]
  /\ turnAccount \in [Turns -> Accounts \cup {NoAccount}]
  /\ turnEpoch \in [Turns -> Nat]
  /\ acquisitionCount \in [Turns -> 0..2]
  /\ settlementCount \in [Turns -> 0..2]
  /\ reservation \in [Turns -> ReservationStates]
  /\ gate \in [Turns -> GateStates]
  /\ gateDeadline \in [Turns -> Nat]
  /\ requestDeadline \in [Turns -> Nat]
  /\ connectDeadline \in [Turns -> Nat]
  /\ firstByteDeadline \in [Turns -> Nat]
  /\ preResponseDeadline \in [Turns -> Nat]
  /\ gateRetireDeadline \in [Turns -> Nat]
  /\ mislabeledKill \in BOOLEAN
  /\ anchor \in [Turns -> [kind : AnchorKinds,
                           account : AnchorOwners,
                           epoch : Nat,
                           lineageOk : BOOLEAN]]
  /\ anchorUsed \in [Turns -> BOOLEAN]
  /\ badAnchorUse \in BOOLEAN
  /\ crossAccountDispatch \in BOOLEAN
  /\ durableVersion \in [Accounts -> Nat]
  /\ snapshotVersion \in [Replicas -> [Accounts -> Nat]]
  /\ routedWithStaleSnapshot \in [Turns -> BOOLEAN]
  /\ snapshotRouteAttempted \in [Turns -> BOOLEAN]
  /\ producerTarget \in [Turns -> Turns]
  /\ terminalReason \in [Turns -> Reasons]
  /\ shutdownPhase \in {"running", "draining", "complete"}
  /\ registered \in [Turns -> BOOLEAN]
  /\ ownerReleased \in [Turns -> BOOLEAN]
  /\ poppedFromPending \in [Turns -> BOOLEAN]
  /\ completedDeliveryClaimed \in [Turns -> BOOLEAN]
  /\ finalizerOwner \in [Turns -> Replicas \cup {NoReplica}]
  /\ finalizerAborted \in [Turns -> BOOLEAN]
  /\ admittedDuringDrain \in BOOLEAN
  /\ clientRetry \in RetryStates
  /\ retryBackoff \in 0..RetryBackoffCap

Inv1AnchorCurrent ==
  badAnchorUse = FALSE

Inv2DeadlineOrdering ==
  \A t \in Turns :
    turnState[t] # "new" =>
      /\ connectDeadline[t] <= firstByteDeadline[t]
      /\ firstByteDeadline[t] <= requestDeadline[t]
      /\ gateDeadline[t] <= requestDeadline[t]

Settled(s) == s \in {"released", "finalized", "transferred"}

Inv3ReservationSettledExactlyOnce ==
  \A t \in Turns :
    /\ acquisitionCount[t] <= 1
    /\ settlementCount[t] <= 1
    /\ (turnState[t] \in TerminalStates /\ acquisitionCount[t] = 1 =>
         /\ settlementCount[t] = 1
         /\ Settled(reservation[t]))
    /\ (settlementCount[t] = 0 => reservation[t] \in {"none", "held"})
    /\ (settlementCount[t] = 1 => Settled(reservation[t]))

Inv4FreshSnapshots ==
  \A t \in Turns : routedWithStaleSnapshot[t] = FALSE

Inv5SingleOwnerCAS ==
  \A a \in Accounts : Cardinality(LiveOnAccount(a)) <= 1

Inv6TerminalIsolation ==
  \A t \in Turns :
    turnState[t] \in TerminalStates =>
      /\ producerTarget[t] = t
      /\ terminalReason[t] # "none"

Inv7GateAccounting ==
  \A t \in Turns :
    /\ gate[t] \in GateStates
    /\ (turnState[t] = "queued" => gate[t] = "queued")
    /\ (turnState[t] \in {"active", "streaming", "completed_delivery_claimed"} => gate[t] = "holding")
    /\ (turnState[t] \in TerminalStates => gate[t] = "terminal")
    /\ (gate[t] = "queued" => gateDeadline[t] <= requestDeadline[t])

Inv8ShutdownDrain ==
  /\ admittedDuringDrain = FALSE
  /\ shutdownPhase = "complete" =>
       \A t \in Turns : registered[t] = FALSE

Inv9TerminalOwnerReleased ==
  \A t \in Turns :
    turnState[t] \in TerminalStates /\ acquisitionCount[t] = 1 =>
      /\ ownerReleased[t]
      /\ turnAccount[t] \in Accounts
      /\ ownerEpoch[turnAccount[t]] = turnEpoch[t] => owner[turnAccount[t]] = NoReplica
      /\ finalizerOwner[t] = NoReplica

(***************************************************************************)
(* Inv10: a request never dispatches with a foreign-owned continuity        *)
(* anchor.  Upstream accepts such a request and then never emits            *)
(* response.created, so the turn can only leave the pre-response phase      *)
(* through a timer or a cancel: the wedge.                                  *)
(***************************************************************************)
Inv10AnchorAccountOwnership ==
  /\ crossAccountDispatch = FALSE
  /\ \A t \in Turns :
       (turnState[t] # "new" /\ anchor[t].kind # "none") =>
         anchor[t].account = turnAccount[t]

(***************************************************************************)
(* Inv11: the pre-response eventless phase has its own bound, derived from  *)
(* the named keepalive budgets, ordered against the owner-side gate-retire  *)
(* bound and strictly separate from the post-start stream-idle bound.  The  *)
(* last conjunct is the behavioural half: no kill may be reported under the *)
(* post-start stream-idle budget while the response had not started.        *)
(***************************************************************************)
Inv11PreResponseBudget ==
  /\ \A t \in Turns :
       turnState[t] = "active" =>
         /\ preResponseDeadline[t] = Min2(Min2(gateRetireDeadline[t], DeadlineFromNow(clock, ClientSafePreResponseCap)), requestDeadline[t])
         /\ preResponseDeadline[t] >= KeepaliveCadenceFloor
         /\ preResponseDeadline[t] <= requestDeadline[t]
  /\ mislabeledKill = FALSE

TurnTermination ==
  \A t \in Turns : (turnState[t] # "new") ~> (turnState[t] \in TerminalStates)

ShutdownEventuallyComplete ==
  shutdownPhase = "draining" ~> shutdownPhase = "complete"

(***************************************************************************)
(* A recoverable tear must eventually be recovered by the client.  This     *)
(* holds only under the bounded-delay assumption on the retry backoff.      *)
(***************************************************************************)
TearEventuallyRecovers ==
  (clientRetry = "torn") ~> (clientRetry = "recovered")

=============================================================================
