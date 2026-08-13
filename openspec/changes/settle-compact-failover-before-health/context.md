Compact timeout already settles the API-key reservation before
`_handle_stream_error`. The generic compact error path did the opposite:
it wrote health, then either surfaced (settle + raise) or failed over
without settling.

`failover_next` cannot release the reservation immediately because the next
account still uses that same reservation. Health is therefore deferred until
the next settle (success, surface, timeout, or exhaustion).
