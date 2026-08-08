# Tasks

## 1. Clock and scheduler seams

- [x] 1.1 Add narrow clock and scheduler adapters with real production defaults.
- [x] 1.2 Thread the clock through the proxy service, load balancer, and HTTP
      bridge retry circuit seams needed by the first harness.
- [x] 1.3 Thread scheduler waits through the selected admission, startup probe,
      HTTP bridge, and WebSocket lifecycle seams.
- [x] 1.4 Thread scheduler task creation through the HTTP bridge and WebSocket
      lifecycle owners so a simulation owns every task a turn spawns.
- [x] 1.5 Declare the clock and scheduler collaborators on the HTTP bridge and
      WebSocket service protocols.
- [x] 1.6 Read the collaborators through `scheduler_for`/`clock_for` inside the
      mixins so partial service objects keep the real production default.

## 2. Deterministic tests

- [x] 2.1 Add a virtual-clock scheduler harness under `tests/simulation/`.
- [x] 2.2 Convert selected admission and startup probe tests from real sleeps
      to virtual time.
- [x] 2.3 Convert the HTTP bridge cancel-drain idle-timeout and recovery-wait
      tests to virtual time.
- [x] 2.4 Add a seeded schedule-exploring bridge lifecycle property test that
      dispatches events as concurrent scheduler tasks.
- [x] 2.5 Add a canary proving the property checker catches a planted
      double-release bug.

## 3. Verification

- [x] 3.1 Run the new and converted deterministic tests three consecutive times.
- [x] 3.2 Run strict OpenSpec validation.
- [x] 3.3 Run the full pytest suite and confirm no new failures beyond the
      documented baseline.
- [x] 3.4 Run `ruff check`, `ruff format --check`, the proxy architecture
      ratchet, and `ty check`.
