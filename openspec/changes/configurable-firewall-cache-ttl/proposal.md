## Why

The API firewall middleware caches per-IP allow/deny decisions to keep
`/v1/*` and `/backend-api/codex/*` request paths off the database for steady
traffic. The cache TTL is currently hard-coded to 2 seconds at module load,
so every IP's decision expires very quickly. Under heavy fan-in (multiple
Codex CLI sessions, Hermes-style local agent stacks, reverse-tunneled
remote clients hitting the same loopback listener) this produces a steady
stream of cache misses, each of which acquires a background DB session to
read the allowlist. When concurrent in-flight cache misses exceed the
background pool capacity (default 25 connections), every new request waits
the full `pool_timeout` and then returns HTTP 500. Client retries then keep
the pool pinned, producing a spiral that lasts until the process is
restarted.

Operators who run codex-lb in front of a fixed set of clients want to relax
the TTL to seconds-or-minutes range so allowlist decisions stay cached
across bursts. Other operators who frequently rotate allowlist entries want
to keep it tight. There is no env-var or configuration knob today.

## What Changes

- add a `firewall_cache_ttl_seconds` setting (env var
  `CODEX_LB_FIREWALL_CACHE_TTL_SECONDS`)
- default to the existing hard-coded value of `2` so behavior does not
  change for operators that do not set it
- construct the process-level `FirewallIPCache` instance with that TTL
  instead of the inline literal

## Impact

Default deployments keep the existing 2-second TTL and identical behavior.
Operators who hit cache-miss-driven DB pool pressure can raise the TTL
(common values: `15`, `60`, `300`) and trade allowlist-change latency for
much lower steady-state DB load. The setting validates `gt=0` so misconfig
fails fast at startup rather than silently disabling the cache.
