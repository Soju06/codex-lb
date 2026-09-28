# Account pool metric design

## Decisions

The pool metrics describe the shared account inventory, not process-local activity. Summing worker snapshots multiplies the inventory; taking a maximum prevents a newer deletion or pause from lowering the observed count. Prometheus `livemostrecent` selects the latest live-worker observation and removes the automatic PID label.

The existing routing cache is seeded at startup and refreshed on account-routing invalidations. Those events alone cannot reflect a token crossing its expiry time while the pool is idle. The metrics ASGI application therefore awaits the same refresh before exposition. It performs one account projection query per scrape, never probes upstream, and fails the scrape if the refresh fails. Operators can use Prometheus `up` alongside availability rather than interpreting stale values as fresh.

The routing cache continues to load its existing complete status map. Metrics omit rows pending deletion, matching the account repository's operator-facing inventory. Only reauthentication-required tokens need decryption for the expiry predicate. Active tokens are counted even when their JWT expiry is past because ordinary routing can refresh them; unknown expiry on a reauthentication-required account remains eligible under the existing predicate.

For example, two active accounts and one reauthentication-required account with a future token expiry produce `accounts_available = 3`. If all three are occupied by streams, the metric remains 3. When the reauthentication token expires, the next scrape reports 2 without changing the status inventory.

## Trade-offs

Scrape-driven refresh adds a database read even on idle replicas. Reusing the
cache projection avoids a separate polling task and keeps freshness tied to the
observation. A per-cache lock serializes overlapping refreshes so an older query
cannot publish over a newer snapshot or discard a concurrent local routing mark.

Availability describes baseline eligibility. Request-specific restrictions can
still prevent routing, and the metrics documentation lists those exclusions.
The change adds no schema or configuration migration. Prometheus remains an
optional runtime dependency; the development group installs it so CI exercises
the exporter and multiprocess behavior.
