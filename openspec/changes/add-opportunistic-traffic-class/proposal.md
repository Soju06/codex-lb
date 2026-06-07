## Why

Background workers can use otherwise-expiring Codex quota, but the proxy is the only component with enough quota, reset, and routing-policy context to decide when that burn is safe.

## What Changes

- Add a per-API-key `traffic_class` with `foreground` as the default and `opportunistic` for burn-only clients.
- Add an opportunistic admission endpoint under the Codex-compatible API surface.
- Route opportunistic requests only through accounts that can be burned without crossing dynamic preserve or emergency foreground floors.
- Add a dashboard-configurable additional-quota routing policy so pools such as Codex Spark can be exhausted independently of the account's standard 5h/7d quota.

## Impact

- Foreground traffic keeps existing routing behavior.
- Opportunistic clients receive standard OpenAI-style `429 rate_limit_exceeded` responses with `Retry-After` when the burn window is closed.
- Models assigned to an additional quota with `burn_first` can keep routing while that additional quota is fresh and available, even if the account's standard Codex quota is exhausted.
