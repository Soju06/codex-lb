## Context

Qualified GPT-5 model aliases are already decomposed into a canonical model,
reasoning effort, and (for the `fast` token) OpenAI's `priority` service tier.
The same request policy is used by HTTP Responses, chat-completions, compact,
and Codex WebSocket traffic. Dashboard settings are persisted in the singleton
`dashboard_settings` row and read through a short-lived cache during request
handling.

## Goals / Non-Goals

**Goals:**

- Let operators disable the priority tier implied by a Codex harness Fast Mode
  alias such as `gpt-5.6-sol-xhigh-fast`.
- Preserve the canonical base model and explicit reasoning level, and apply the
  effective policy before model-source selection, quota reservation, and
  OpenAI forwarding.
- Keep the setting immediately effective through the existing settings cache
  invalidation path and expose it in the dashboard routing section.

**Non-Goals:**

- Rejecting requests that use a Fast Mode alias.
- Changing an explicit `service_tier` supplied by a client or enforced by an
  API key.
- Reclassifying unrelated model IDs such as `gpt-5.3-codex-spark`.

## Decisions

### Persist a disabled-by-default `prohibit_fast_mode` dashboard setting

The setting belongs to the existing dashboard settings singleton because it is
an operator-wide routing policy, not an API-key policy or process-startup
configuration. A non-null boolean with a `false` database default makes both
fresh installs and historical rows safely retain current behavior.

### Suppress only the priority tier derived from a qualified `-fast` alias

The model-alias normalizer will receive the resolved dashboard policy. When it
sees the `fast` suffix while prohibition is enabled, it continues assigning the
canonical model and alias-derived reasoning effort but does not add
`service_tier: "priority"`. This is narrower than stripping any priority tier:
an explicit tier and API-key enforcement remain deliberate separate contracts.

### Resolve the setting at each request entry point

HTTP entry points will resolve the cached setting before their first policy
pass, before source selection and quota accounting. The long-lived Codex
WebSocket session will snapshot it when the connection starts and pass it to
each response-create preparation call. This keeps a session internally
consistent while avoiding a database/cache read for every WebSocket frame.

## Risks / Trade-offs

- [A user expects all priority requests to be blocked] → The dashboard copy and
  setting contract explicitly describe qualified Fast Mode aliases; explicit
  tiers remain unchanged by design.
- [A setting change occurs during an open WebSocket connection] → The change
  takes effect for the next connection; HTTP requests use the refreshed cache
  immediately after the existing invalidation path runs.
- [Historical databases lack the column] → The migration adds it with a server
  default and the ORM/repository create path supplies `false`.

## Migration Plan

1. Deploy the additive migration and code with the default disabled.
2. Operators enable the dashboard switch when they need Fast Mode aliases to
   run at the normal upstream tier.
3. Rolling back code leaves the additive column harmless; migration downgrade
   removes the column only when a full schema rollback is required.
