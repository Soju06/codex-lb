# Reset-credit observation context

## Paused account observation

A paused account's weekly reset time is rendered from the last persisted usage sample. It is not evidence that background usage polling continues while paused. Similarly, reset-credit list badges are last observed in-memory snapshots, not a live freshness guarantee; they can be absent after restart. The selected-account usage-reset-credits endpoint independently reads the current count, so inspecting a paused account does not require resuming traffic.

This separation follows credential maintenance: [usage-refresh-policy](../usage-refresh-policy/spec.md) already allows Auth Guardian to refresh paused credentials while preserving routing exclusion. Count reads reuse the same AuthManager, including a forced refresh and retry after an upstream 401. Successful observation keeps the account paused; permanent authentication failures retain the existing documented status transitions.

For example, an operator opens a paused account and sees `2 available` alongside a disabled reset action. After a server restart the summary may initially have no cached badge, but opening the detail still reads its count. A failed live read uses the existing unavailable/error state instead of reporting a successful zero count. When old query data remains cached, the UI may retain that last successful value during a later failed fetch, matching its existing query behavior.

No new background network activity is enabled for paused accounts. Both manual consumption paths and automatic redemption remain blocked. Account-bound proxy routing still fails closed; a paused import awaiting a usable proxy route cannot bypass that restriction to inspect credits. Deactivated and reauth-required policies are unchanged, including the existing difference between reauth summary-cache projection and cached-detail invalidation.

The normative contracts are [reset-credit visibility](spec.md), [routing](../account-routing/spec.md), and [upstream proxy routing](../upstream-proxy-routing/spec.md). No schema migration or setting is needed.
