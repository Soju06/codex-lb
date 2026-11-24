# Refresh headroom

The producer currently publishes a generation and schedules its replacement for
`generated_at + interval`. Consumers without freshness grace already consider the
generation expired at that instant; consumers with grace spend part of that allowance
waiting for healthy work. Collection and scheduler delay therefore create an
avoidable gap rather than necessarily indicating a failed producer.

Schedule the next collection at `generated_at + max(1, interval - headroom)`, where
headroom is the existing 90-second collector deadline plus ten seconds for two
scheduler ticks. This is a bounded cadence, not a loop that immediately recollects
short-interval values. Keep generation timestamps and reader/readiness freshness
tests unchanged. A genuinely stalled or failed producer must still become stale.

For a five-minute value, collection becomes due after 200 seconds, leaving 100
seconds for collection and ordinary scheduling delay. Existing leases, generation
fences and cancellation retain ownership. This does not promise freshness under
unbounded queued work or repeated failures.

The alternative of merely extending the freshness window hides the avoidable gap
without starting the replacement earlier. Request-time collection would reintroduce
slow reads. Neither is needed for this correction.

Verify the actual repository's publication, due/readiness and stale/failure paths
against a real isolated database. Compare the unmodified deployed source as a causal
control. Production adoption requires a separately qualified immutable candidate;
never edit the selected process's source or force its clients to disconnect.


The standalone browser smoke backend keeps the local snapshot producer enabled
while suppressing outbound catalog, usage and maintenance loops. It waits for
analytical readiness before launching the browser; infrastructure liveness alone
does not imply that prepared dashboard values exist. Integration tests that
disable all schedulers explicitly publish a replacement after changing source
state before asserting the prepared overview.
