## 1. Regression Coverage

- [x] 1.1 Add a deterministic real-socket test proving a sole abandoned browser flow releases the callback port and local state at expiry without a follow-up request; verify it fails before the implementation and passes afterward.
- [x] 1.2 Add deterministic overlapping-flow coverage proving the listener survives the earlier deadline and stops after the final deadline.
- [x] 1.3 Add concurrency, early-completion, and reset coverage proving a start racing listener shutdown receives a live replacement and retired deadline work is drained.
- [x] 1.4 Add cancellation and startup/reset overlap coverage proving listener startup cannot become untracked.
- [x] 1.5 Add transient-shutdown and post-persistence cancellation coverage proving cleanup remains process-owned without a later request.

## 2. OAuth Listener Lifecycle

- [x] 2.1 Add one store-owned browser-flow deadline task with a dedicated sleep seam; verify it prunes due local flows, recomputes overlapping deadlines, and identity-guards its task slot.
- [x] 2.2 Schedule the deadline task for successfully started browser flows and cancel/await it during full store reset; verify no task crosses test boundaries.
- [x] 2.3 Serialize browser-flow insertion against a callback-server stop registered after the initial wait; verify the concurrent-start regression passes.
- [x] 2.4 Own callback-server startup, retrying shutdown, and terminal cleanup so cancellation, reconciliation, and reset cannot orphan runtime work.
- [x] 2.5 Fence browser-start persistence and durable reconciliation against full-store reset with store generations.

## 3. Validation

- [x] 3.1 Run the focused listener-lifecycle tests and the complete OAuth integration test file.
- [x] 3.2 Run Ruff check/format and `ty` for the affected backend code.
- [x] 3.3 Run strict validation for this OpenSpec change and the main spec tree, then perform a completeness/correctness/coherence verification against the finished diff. The change passes strictly; the main tree remains 57/58 because of the pre-existing `model-source-routing` format failure.

## 4. Docker Host-Port Safety

- [x] 4.1 Remove default host-port 1455 publication from the portable Docker commands and both shipped Compose files while retaining port 2455.
- [x] 4.2 Document device-code and manual-callback account setup, the dedicated-host opt-in mapping, and the container-recreation requirement for existing installs.
- [x] 4.3 Add a hermetic unit contract proving stock Compose and documented default launch paths keep host port 1455 free.
