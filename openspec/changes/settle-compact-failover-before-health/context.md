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
