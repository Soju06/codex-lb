## Tasks

- [x] Add OpenSpec requirements for bounded HTTP bridge startup waits.
- [x] Bound per-session response-create gate acquisition.
- [x] Bound HTTP bridge capacity and in-flight session waits.
- [x] Clean up in-flight markers when creation owners are cancelled during stale session close.
- [x] Evict stalled in-flight markers on startup wait timeout.
- [x] Log timeout diagnostics without raw affinity keys.
- [x] Retire HTTP bridge sessions whose precreated replay cannot make progress after upstream disconnect.
- [x] Clear stale pending bridge state even when terminal request-log writing fails.
- [x] Reject concurrent response-create gate and prewarm waiters before they can submit on a retired stale session.
- [x] Reject unregistered stale session references after response-create admission.
- [x] Make post-admission validation, enqueue, and send mutually exclusive with stale-session retirement.
- [x] Reject unregistered closed stale session references before reconnect attempts.
- [x] Mark reader-crashed bridge sessions closed before releasing pending response-create gates.
- [x] Retire reader-crashed bridge sessions from local reuse and release resources.
- [x] Preserve visible queue counts when prewarm cleanup runs.
- [x] Move admission/retirement serialization off the global bridge registry lock.
- [x] Add regression tests for timeout behavior.
- [x] Validate OpenSpec and run targeted unit tests.
