# Remove the upstream stream transport environment variable

## Why

`upstream_stream_transport` is configured in two places. The dashboard row
accepts `default | auto | http | websocket`, where `default` is a sentinel
meaning "defer to `CODEX_LB_UPSTREAM_STREAM_TRANSPORT`", whose own default is
`auto`. The Helm chart templates the environment variable and
`docs/client-setup.md` tells operators to export it, while the dashboard
owns the value. An operator who sets the Helm value sees "Server default" in
the dashboard and cannot tell which source applies without reading code.

The configuration policy for this repository is a single fixed precedence
(code default < environment < dashboard) and no environment variable for a
value the dashboard already owns unless it is a bootstrap or per-replica
topology value. The upstream transport is neither.

## What Changes

- **BREAKING**: `CODEX_LB_UPSTREAM_STREAM_TRANSPORT` is removed from
  `Settings` and listed under removed settings (startup WARN if still set).
  The dashboard setting is the only source.
- The dashboard literal set becomes `auto | http | websocket`; the `default`
  sentinel is removed from the API schema, the ORM default, the first-boot
  seed, and the dashboard select.
- Data migration maps existing `default` rows to `auto` (the documented
  default of the removed variable) and changes the column server default to
  `auto`. A migration cannot read the environment the removed variable was
  set in: an operator who pinned the variable to `http` or `websocket`
  must pin the transport in the dashboard after upgrading.
- Runtime readers (stream retry, websocket route 426 gate, HTTP bridge
  bypass) resolve the transport from the dashboard snapshot through one
  helper; a snapshot still carrying `default` (cached before the migration)
  resolves to `auto`.
- The core proxy client no longer reads the transport from `Settings`; the
  service passes the dashboard-resolved value as the override and callers
  that pass none (warmup probes, bridge owner forwarding) use `auto`, which
  was the environment default.
- Helm `configmap.yaml` / `values.yaml` drop the variable; `docs/client-setup.md`
  and `docs/traffic-parity.md` point to the dashboard setting; the settings
  reference is regenerated (surface ratchet 135 -> 134).

## Impact

- Deployments that set `CODEX_LB_UPSTREAM_STREAM_TRANSPORT` (including Helm
  `config.upstreamStreamTransport`) get a startup WARN and the dashboard
  value applies; `auto` unless the dashboard was already pinned.
- `PUT /api/settings` rejects `upstreamStreamTransport: "default"`.
- Dashboard: the "Server default" option disappears from the upstream stream
  transport select.

## Non-goals

- No change to the `auto` decision itself (native headers, model preference,
  payload budget, websocket fallback).
- No change to `http_downstream_transport_policy`.
