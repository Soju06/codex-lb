Compact timeout already settles the API-key reservation before
`_handle_stream_error`. The generic compact error path did the opposite:
it wrote health, then either surfaced (settle + raise) or failed over
without settling.

`failover_next` cannot release the reservation immediately because the next
account still uses that same reservation. Health is therefore deferred until
the next settle (success, surface, timeout, or exhaustion).

`_settle_compact_api_key_usage` still raises `usage_settlement_failed` after
a finalize failure even when its fail-safe release succeeds. That exception
must carry whether the reservation is actually released so deferred health
can flush before the 502 is surfaced. If the fail-safe release also fails,
the reservation is still held and deferred health stays queued.

Once usage is finalized, a later deferred health-write failure is a local
persistence problem. It must not convert a billed compact success into a 500
that clients retry. Cancellation and other non-proxy exits skip the dedicated
settle handlers, so they need an explicit settle-then-flush before the
original exception continues. The flush itself is shielded so a cancel that
arrives after queues are drained cannot drop the remaining health write.
Each deferred entry is written independently so one persistence failure does
not skip the other failed accounts. A later `_select_account_with_budget`
timeout is a `ProxyResponseError`, so it must use the same settle-and-flush
cleanup as other unsettled exits.
