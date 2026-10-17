------------------------ MODULE StreamIdleRegression ------------------------
EXTENDS CoreOwnership

VARIABLE step
scenarioVars == <<vars, step>>
T == CHOOSE t \in Turns : TRUE
R == CHOOSE r \in Replicas : TRUE
A == CHOOSE a \in Accounts : TRUE

RegressionInit == Init /\ step = 0

\* Four progress events each follow one tick. Total streaming time exceeds
\* the three-tick request budget, then three silent ticks must expire it.
RegressionNext ==
  \/ /\ step < 18
     /\ CASE step = 0 -> QueueTurn(T)
          [] step = 1 -> RouteFromSnapshot(T, R, A)
          [] step = 2 -> AcquireTurn(T, R, A, NoAccount)
          [] step = 3 -> UpstreamConnected(T)
          [] step = 4 -> UpstreamFirstByte(T)
          [] step = 5 -> StartStream(T, "client_anchor")
          [] step \in {6, 8, 10, 12, 14, 15, 16} -> Tick
          [] step \in {7, 9, 11, 13} -> StreamProgress(T)
          [] step = 17 -> ExpireDeadline(T)
     /\ step' = step + 1
  \/ /\ step = 18
     /\ UNCHANGED scenarioVars

RegressionInvariant ==
  /\ (step \in 6..17 => turnState[T] = "streaming")
  /\ (step \in 6..17 => ExpireBoundFor(T) = StreamIdleBudget)
  /\ (step \in {6, 8, 10, 12, 14} => phaseElapsed[T] = 0)
  /\ (step = 17 => phaseElapsed[T] = StreamIdleBudget)
  /\ (step = 18 => turnState[T] = "failed" /\ terminalReason[T] = "timeout")

RegressionCompletes == <> (step = 18)
RegressionSpec == RegressionInit /\ [][RegressionNext]_scenarioVars /\ WF_scenarioVars(RegressionNext)
=============================================================================
