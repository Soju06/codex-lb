## Tasks

- [x] Add OpenSpec requirements for bounded HTTP bridge startup waits.
- [x] Bound per-session response-create gate acquisition.
- [x] Bound HTTP bridge capacity and in-flight session waits.
- [x] Clean up in-flight markers when creation owners are cancelled during stale session close.
- [x] Evict stalled in-flight markers on startup wait timeout.
- [x] Log timeout diagnostics without raw affinity keys.
- [x] Retire HTTP bridge sessions whose precreated replay cannot make progress after upstream disconnect.
- [x] Clear stale pending bridge state even when terminal request-log writing fails.
- [x] Add regression tests for timeout behavior.
- [x] Validate OpenSpec and run targeted unit tests.
