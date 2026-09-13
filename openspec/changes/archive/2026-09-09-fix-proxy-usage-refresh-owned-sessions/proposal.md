# Own proxy usage refresh sessions

Proxy usage payload refreshes must remain safe when the request task is cancelled
and must not retain a database session across upstream usage I/O.

## Scope

- Detach rows loaded for the pre-refresh decision before closing the request-adjacent repository scope.
- Keep refresh reads and writes on caller-independent background sessions.
- Reopen the repository scope only for post-refresh payload reads.
- Add route-level regression coverage for cancellation and detached-row safety.
