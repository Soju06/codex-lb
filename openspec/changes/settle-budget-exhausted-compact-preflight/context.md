# Compact Preflight Reservation Context

## Purpose and Scope

This change closes one reservation-lifecycle gap in forwarded compact requests. It does not change compact retry policy, timeout budgets, API-key limit calculation, or stale-reservation cleanup.

## Ownership Constraint

The HTTP bridge reserves API-key quota before forwarding and passes the reservation into the stream path as an override. That caller reports `owns_reservation = false`; during compact handling, `compact_responses` is therefore the only component that can settle the reservation before returning control.

## Failure Mode

If the compact request budget expires before or immediately after account freshness work, `_raise_proxy_budget_exhausted()` raises `502 upstream_request_timeout`. The outer `ProxyResponseError` handler only records failure metadata, and the finalizer only writes the request log. Without an explicit settlement at the terminal exit, the reservation remains `reserved` and continues contributing to API-key limits.

## Decision and Alternative

The fix settles immediately before each leaking terminal raise. A broad outer `finally` cleanup was considered, but compact success, retry, and upstream-attempt paths already have distinct finalize/release behavior; unconditional cleanup there risks double settlement or releasing usage that should be finalized. The narrow terminal-site fix preserves those ownership boundaries.

## Concrete Example

An HTTP-bridge request reserves an 8,192-token input budget and a 2,048-token output budget, then exhausts its proxy budget during compact freshness preflight. The client still receives the normal 502 timeout. With this change, the unused reservation is released before that response, so a subsequent request is evaluated against actual usage rather than the abandoned 10,240-token hold.

## Operational Note

No migration or rollout sequencing is required. Existing stale-reservation cleanup remains a safety net, but it is no longer the normal recovery path for these exits.
