# Anonymous telemetry

codex-lb sends an anonymous usage snapshot to the project-operated collector at
`https://telemetry.tokmaxxing.com` when the service starts and every 24 hours. In a
multi-replica deployment, only the elected leader builds and sends the snapshot.

## What is sent

Before the first consent decision, the dashboard shows the current JSON envelope. You can also
view it later from Settings. The signed snapshot body has three fields:

```json
{
  "instance_id": "<random UUIDv4>",
  "metrics": { "<schema below>": "..." },
  "timestamp": "<current UTC time>"
}
```

The versioned `metrics` schema contains only these fields:

- `schema_version`, active `consent` (`undecided` or `enabled`), random `instance_id`, codex-lb
  `version`, Python version, OS, architecture, and process uptime
- `deploy`: deployment method, database backend and size bucket, replica count, and whether
  trusted reverse-proxy headers are enabled
- `accounts`: bucketed pool and plan counts, whether workspace accounts exist, routing policy,
  limit warmup, and whether an egress proxy is used
- `usage_7d`: request/success/token aggregates, bucketed cost, request-kind and transport/service
  tier shares, allowlisted client families and model names, bucketed output-token averages,
  latency percentiles, rate-limit ratio, and allowlisted upstream error codes
- `features`: booleans for optional features plus bucketed API-key count and model-source count

Registration sends `app_name`, `app_version`, `deployment_mode`, an intentionally empty
`environment`, the random `instance_id`, coarse `os_arch`, and the Ed25519 `public_key` used to
verify signed updates. Activation sends only `{"action": "activate"}`.

The schema never includes account emails, workspace identifiers, client IP addresses, API keys,
request or response content, raw user-agent strings, per-account records, custom model names, or
free-text errors. Exact schemas and privacy constraints live in the
[telemetry OpenSpec capability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/telemetry).

## Consent and disabling

Telemetry uses informed opt-out consent. With no override or saved decision, it is active and
the dashboard presents a one-time dialog with the current payload. Enabling or disabling saves
the decision, and the Settings toggle can change it later.

For a headless or deployment-level kill switch, set:

```bash
CODEX_LB_TELEMETRY_ENABLED=false
```

An environment value overrides the saved dashboard setting. When telemetry resolves to
disabled, codex-lb opens no connection to the telemetry endpoint. The environment kill switch
is always completely silent.

When a dashboard decision changes telemetry from active to inactive, codex-lb makes one final
signed request to `POST /v1/optout` so aggregate opt-out counts remain accurate. If the instance
has not contacted the collector in this process, it first performs the normal registration and
activation. Re-enabling and later disabling from the dashboard sends one new notice for that new
transition. Repeating an already-disabled decision sends nothing.

The opt-out request uses the same `X-Instance-ID` and Ed25519 `X-Signature` headers as a snapshot.
Its canonical JSON body is:

```json
{
  "app_version": "<codex-lb version>",
  "event": "optout",
  "instance_id": "<random UUIDv4>",
  "occurred_at": "<current ISO 8601 UTC time>"
}
```

This single decision-time notice is the only exception to disabled telemetry silence. It is
sent only for a dashboard-driven active-to-inactive transition; setting
`CODEX_LB_TELEMETRY_ENABLED=false`, or changing a saved decision while either environment
override value controls telemetry, never sends it.

## Retention and failures

Each snapshot summarizes the previous seven days of data already present in `request_logs`.
codex-lb does not keep a separate local telemetry history and does not queue a failed send. The
collector's server-side retention duration is not currently specified; assume transmitted
snapshots remain stored until a published retention policy or explicit deletion.

Snapshot and opt-out endpoint failures use a five-second total timeout, retry no more than once,
are logged only at debug level, and never interrupt proxy traffic or change the dashboard
settings response.

*Source of truth: [telemetry OpenSpec capability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/telemetry)*
