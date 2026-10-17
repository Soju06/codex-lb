# Fast Mode Policy Context

## Purpose and Scope

`prohibitFastMode` is an operator-wide control for preventing OpenAI priority
service-tier requests across the proxy. It covers explicit client fields,
Fast Mode model aliases, API-key enforcement, forwarded requests, and other
resolved defaults while preserving the selected model and reasoning effort.

## Decision Rationale

The setting lives with dashboard routing controls because operators use it to
control account consumption across the entire proxy. An administrator-enabled
global prohibition therefore takes precedence over a narrower API-key policy:
an enforced priority tier cannot bypass the operator control.

Policy resolution happens after request-tier writers have run but before
source selection, quota reservation, request logging, and serialization. This
keeps internal routing and accounting aligned with the payload sent upstream.
The shared policy removes priority by setting the typed field to `None` (or
removing it from a source-chat dictionary), because wire-level absence is the
existing representation of the upstream default. It does not substitute the
literal `"default"` value.

## Constraints and Failure Modes

- Both `fast` and `priority` canonicalize to the prohibited priority identity;
  unrelated service tiers remain unchanged.
- Existing requests remain unchanged until an operator enables the setting;
  this preserves rollout compatibility.
- API-key enforcement provenance remains intact even when its resulting
  priority tier is later removed by the global policy.
- HTTP requests observe a refreshed dashboard setting through normal cache
  invalidation. A connected Codex WebSocket uses the policy snapshot taken
  when it connected, so reconnect it after changing the setting when immediate
  WebSocket behavior is required.

## Examples

With the switch enabled, this harness request:

```json
{"model":"gpt-5.6-sol-xhigh-fast","input":"review this change"}
```

is forwarded as `gpt-5.6-sol` with `reasoning.effort: "high"` and without a
`service_tier`.

The same omission applies to an explicit request:

```json
{"model":"gpt-5.6-sol","input":"review this change","service_tier":"priority"}
```

and to an API key that enforces priority. With the switch disabled, these
requests retain the existing priority-tier behavior.

## Operational Notes

Enable the setting from Settings → Routing when normal-tier requests are
required. New HTTP requests use the updated policy immediately; reconnect
long-lived Codex WebSockets. The info log
`fast_mode_service_tier_prohibited` records each removal with the request ID
and stripped value.
