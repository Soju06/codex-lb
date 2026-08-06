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

The first schedule checker models the recurring lease and terminal-state bug
class from the taxonomy: a bridge turn must reach exactly one terminal outcome
and release response-create, API-key, and account leases exactly once even when
admission, upstream terminal delivery, downstream cancellation, and retry
attachment interleave. Its planted-bug canary double-releases on cancel followed
by a late upstream terminal event, proving the checker fails a known bad state
machine.
