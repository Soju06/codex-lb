## 1. Exact-owner timeout recovery

- [x] 1.1 Record owner-task provenance on in-flight HTTP bridge creation markers.
- [x] 1.2 After capacity and same-key timeouts, abort only the exact owner and retry only after it terminates.
- [x] 1.3 Preserve the existing structured 429 when the exact owner outlives the bounded wait.

## 2. Regression coverage and verification

- [x] 2.1 Cover successful post-cancellation admission for same-key and capacity waiters.
- [x] 2.2 Cover cancellation-resistant owners remaining capacity-owned while the waiter returns 429.
- [x] 2.3 Cover settled retained markers: unrelated capacity waiters never inherit the creator's rejection, cancelled markers do not spin, wedged owners are reclaimed by the sweeper.
- [x] 2.4 Cover echoed generated turn-state classified from the recorded alias through the streaming entry point, and forwarded keys carrying signed provenance.
