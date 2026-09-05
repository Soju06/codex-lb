# Context

This change is deliberately a first harness, not a complete conversion of every
proxy timing seam. The seam audit found hundreds of direct time reads and more
than one hundred sleeps or waits. Rewriting all of them at once would make the
review about incidental plumbing instead of proving one deterministic lifecycle
slice.

The production adapters default to real `asyncio` and real time. Tests opt into
virtual time by constructing services or controllers with explicit clock and
scheduler objects. The virtual scheduler runs on the pytest loop but owns its
timers and task registry, so tests can advance deadlines without wall-clock
sleep and can cancel owned tasks at the reset boundary.

Inside the proxy mixins the seam is read through `scheduler_for` and `clock_for`
rather than a bare attribute. Bridge and websocket lifecycle methods are also
called with partial service doubles, so requiring every caller to carry a
collaborator would turn a test-only construction detail into a runtime failure.
The accessors fall back to the real scheduler and real clock, which is the same
production default the constructors use.

The audit's third injection item, an upstream transport factory, is deliberately
left out. The converted tests reach their upstream through the existing fake SSE
and fake websocket doubles, so a transport factory would be new production
surface with no consumer yet. It stays available for the next slice.

The first schedule checker models the recurring lease and terminal-state bug
class from the taxonomy: a bridge turn must reach exactly one terminal outcome
and release response-create, API-key, and account leases exactly once even when
admission, upstream terminal delivery, downstream cancellation, and retry
attachment interleave.

The checker drives production code rather than a model of it. Release runs
through `_release_websocket_response_create_ownership_for_cleanup` and
`ProxyService._release_websocket_request_state_reservation`, and the admission
wait contends for a permit on a real `WorkAdmissionController`. Every event in a
schedule is a concurrent task with its own seeded virtual deadline, so equal
deadlines produce real interleaving at the await points inside those helpers.

Its planted-bug canary releases the create ownership on the cancel path before
the shared cleanup takes ownership of it, so a cancel racing an upstream
terminal double-releases. That proves the checker fails a known bad state
machine. The same checker also rejects a lost terminal claim, a dropped API-key
reservation release, and a permit that is never handed back to the admission
gate.
