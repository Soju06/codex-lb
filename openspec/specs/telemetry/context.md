# Telemetry capability — context

## Purpose

Give the project visibility into its install base (version distribution, deployment shapes,
client ecosystem, feature usage) without collecting anything that identifies an operator,
an account, or request content. Consent model is informed opt-out: active by default,
one-time dialog with the exact payload, settings toggle, env kill switch.

Decision record (2026-08-06, maintainer): default-on with first-run confirmation dialog for
both new and existing users; settings toggle; expanded field set over the minimal version.

## Collection endpoint

Self-hosted SHM (kOlapsis/shm) server operated by the maintainer at
`https://telemetry.tokmaxxing.com`. SHM provides Ed25519 instance signing, aggregate dashboards,
and public README badges (`/badge/codex-lb/instances`, `/badge/codex-lb/version`).
The SDK path is `/v1/register`, `/v1/activate`, `/v1/snapshot` (note: NOT `/api/v1/`,
which is SHM's admin namespace). codex-lb implements a small Python client (SHM ships
Go/Node SDKs only).

## Payload schema v1 (the allowlist)

Everything below derives from existing data (`request_logs`, settings, module registry).
No new per-request instrumentation. `*_bucket` fields use the documented bucket sets.

Every outbound request body has an explicit Pydantic model and is covered by the wire-schema
allowlist test. The registration body sent to `/v1/register` is:

```json
{
  "app_name": "codex-lb",
  "app_version": "1.20.2",
  "deployment_mode": "docker | k8s | pip | bare",
  "environment": "",
  "instance_id": "<random UUIDv4>",
  "os_arch": "linux/x86_64",
  "public_key": "<Ed25519 public key, hex>"
}
```

`app_name` identifies this project, `app_version` supports upgrade/deprecation decisions,
`deployment_mode` and `os_arch` are coarse deployment signals, `environment` is intentionally
empty, and `instance_id` plus `public_key` establish the random signing identity. Activation
sends only `{"action": "activate"}`.

The signed `/v1/snapshot` body is an envelope. The consent preview renders this same shape via
the same constructor; its timestamp is the current preview-generation time, while an actual
send regenerates the current transmission time.

```json
{
  "instance_id": "<random UUIDv4>",
  "metrics": { "<snapshot metrics below>": "..." },
  "timestamp": "2026-08-06T12:00:00Z"
}
```

The `metrics` object is the versioned snapshot schema:

```json
{
  "schema_version": 1,
  "instance_id": "<random UUIDv4, minted on first run>",
  "version": "1.20.2",
  "python": "3.13",
  "os": "linux",
  "arch": "x86_64",
  "uptime_hours": 168,

  "deploy": {
    "method": "docker | k8s | pip | bare",
    "db_backend": "sqlite | postgres",
    "db_size_bucket": "unknown | <bucket>",
    "replicas": 3,
    "reverse_proxy": true
  },

  "accounts": {
    "pool_bucket": "<bucket>",
    "plan_mix": {"plus": "<bucket>", "pro": "<bucket>", "team": "<bucket>", "free": "<bucket>"},
    "workspace_accounts": true,
    "routing_policy": "<enum>",
    "limit_warmup_enabled": true,
    "egress_proxy_used": false
  },

  "usage_7d": {
    "requests": 203051,
    "success_rate": 0.987,
    "tokens_input": 18800000000,
    "tokens_output": 94000000,
    "tokens_cached_ratio": 0.89,
    "cost_usd_bucket": "<bucket>",
    "request_kinds": {"responses": 0.0, "chat": 0.0, "images": 0.0, "unknown": 1.0},
    "transport_mix": {"ws": 0.6, "http_bridge": 0.4},
    "service_tier_mix": {"default": 0.90, "flex": 0.05, "priority": 0.05},
    "clients": {"codex-cli": 0.44, "openai-sdk-python": 0.3, "other": 0.02},
    "clients_other_ratio": 0.02,
    "models": [
      {
        "name": "gpt-5.4-codex",
        "share": 0.62,
        "reasoning": {"xhigh": 0.31, "high": 0.48, "medium": 0.21},
        "avg_output_tokens_bucket": "<bucket>"
      }
    ],
    "latency_ms_p50": 1200,
    "ttft_ms_p50": 800,
    "ttft_ms_p95": 3400,
    "rate_limit_429_ratio": 0.004,
    "top_upstream_errors": ["server_overloaded", "usage_limit_reached"]
  },

  "features": {
    "api_firewall": true,
    "quota_planner": true,
    "sticky_sessions": true,
    "conversation_archive": false,
    "automations": false,
    "fleet": false,
    "model_sources_count": 2,
    "api_keys_bucket": "<bucket>",
    "prometheus": false,
    "otel": false,
    "dashboard_auth": true,
    "reset_credits": true,
    "image_api_used": true
  }
}
```

Field notes:

- `top_upstream_errors`: enum `upstream_error_code` values only, top 5 by count. Free-text
  `error_message` is banned by spec.
- `request_kinds`: current `request_logs` rows do not persist ingress route family. The existing
  `request_kind` column is a workload class (`normal`, `warmup`, `compaction`, and similar),
  while `source` identifies the upstream. Until an authoritative route-family signal exists,
  rows are reported as `unknown`; source and model-name heuristics are deliberately forbidden.
- `clients`: canonical family shares from the normative mapping table in `spec.md`. Raw
  `useragent_group` values never leave the instance.
- `models[].name`: official model catalog allowlist match; custom/unknown model names fold
  into a single `{"name": "other"}` entry.
- Exact `requests` / token counts are transmitted raw deliberately: they power the global
  aggregate counter story and cannot identify an instance. Everything correlated with spend
  or org size (accounts, keys, cost, DB size) is bucketed.
- `replicas`: size of the configured HTTP bridge instance ring (multi-replica adoption signal).

## Bucket sets

- count buckets (accounts, api keys, plan mix): `0`, `1`, `2-5`, `6-20`, `21-100`, `100+`
- `db_size_bucket`: `unknown`, `<100MB`, `100MB-1GB`, `1-5GB`, `5-10GB`, `10-50GB`, `50GB+`
- `cost_usd_bucket` (7d): `<10`, `10-100`, `100-1k`, `1k-10k`, `10k-50k`, `50k+`
- `avg_output_tokens_bucket`: `<250`, `250-1k`, `1k-4k`, `4k-16k`, `16k+`

## Consent resolution precedence

`CODEX_LB_TELEMETRY_ENABLED` env (when set) > persisted decision > default
(`undecided` ⇒ active). The dialog is only shown while persisted state is `undecided` and
no env override exists.

## Consent API and preview cost

`GET /api/settings/telemetry` always returns `state`, `source`, `active`, and `preview`. The
default GET includes a preview envelope only for undecided/default consent, when the dialog can
appear; decided and environment-overridden responses return `preview: null` without running the
seven-day aggregate queries. Settings requests the same endpoint with
`include_preview=true` to fetch the current envelope on demand. `PUT /api/settings/telemetry`
persists the decision and returns `preview: null`.

## Cadence and replica ownership

The startup and 24-hour ticks run through the shared scheduler leader-election gate. Only the
leader constructs aggregates, transmits the snapshot, and logs the undecided-consent startup
notice. Followers perform none of that work, avoiding duplicate snapshots and duplicate notices.

## Retention

Each snapshot summarizes the previous seven days of existing local request logs. codex-lb does
not create a second local telemetry history or queue failed transmissions. The project-operated
collector is `https://telemetry.tokmaxxing.com`; its server-side retention duration is not yet
specified, so operators should assume transmitted snapshots remain stored until a published
retention policy or explicit deletion.

## Failure modes

- Endpoint down: bounded timeout (5s), at most one retry per interval, debug-level log,
  proxy path untouched. Snapshot is rebuilt fresh next interval (no queue/backlog).
- Aggregation query cost: snapshot queries reuse the same 7-day aggregate shapes as the
  dashboard reports module; they run on the leader scheduler once per tick and only on an API
  request when the undecided dialog or an explicit settings preview needs them. On Postgres
  instances with very large `request_logs` this is the same load class as one dashboard load.
- Clock skew / restart loops: the elected leader transmits the startup snapshot; SHM's
  `/v1/activate` is idempotent (active → active refreshes last-seen). Rapid restart loops are
  bounded by one snapshot per elected-leader process start; no local rate limiter in v1.

## Example: privacy review quick check

An instance with accounts `alice@corp.com` (workspace W1) + 12 others, a custom model source
`corp-internal-gpt`, and traffic from an internal tool `senpi/1.0`:

- payload has `pool_bucket: "6-20"`, `workspace_accounts: true`
- `corp-internal-gpt` traffic appears as `models[].name == "other"`
- `senpi` traffic appears in `clients` under `other` and inflates `clients_other_ratio`
- the strings `alice`, `corp.com`, `W1`, `corp-internal-gpt`, `senpi` appear nowhere in the
  serialized payload (schema snapshot test enforces this)
