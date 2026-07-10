# Fast Mode Policy Context

## Purpose and Scope

`prohibitFastMode` is an operator-wide routing preference for Codex harness
model labels that end in the known `fast` suffix. It keeps the request on the
same canonical OpenAI model and preserves the selected reasoning effort while
preventing the alias from opting into the priority tier.

## Decision Rationale

The setting lives with dashboard routing controls because operators normally
use it to control account consumption across the entire proxy, not one API
key. It acts during alias normalization, before source selection and quota
reservation, so downstream routing observes the normal-tier request rather
than only changing an already-built wire payload.

The policy is intentionally narrower than disabling all `priority` traffic.
Explicit `service_tier` values and API-key service-tier enforcement are
separate caller and operator contracts and remain intact.

## Constraints and Failure Modes

- It applies only to supported qualified GPT-5 aliases containing the `fast`
  token. It does not reinterpret unrelated slugs such as Codex Spark.
- Existing requests remain unchanged until an operator enables the setting;
  this preserves rollout compatibility.
- HTTP requests observe a refreshed dashboard setting through the normal
  settings-cache invalidation path. A connected Codex WebSocket uses the
  policy snapshot taken when it connected, so reconnect it after changing the
  setting when immediate WebSocket behavior is required.

## Example

With the switch enabled, this harness request:

```json
{"model":"gpt-5.6-sol-xhigh-fast","input":"review this change"}
```

is forwarded as the `gpt-5.6-sol` model with `reasoning.effort: "high"` and
without a `service_tier`. With the switch disabled, the same alias derives the
usual `service_tier: "priority"` request.

## Operational Notes

Enable the setting from Settings → Routing when normal-tier requests are
required for Codex harness Fast Mode labels. Check the request log's requested
service tier after a new HTTP request; it will be absent for a prohibited Fast
Mode alias unless another explicit policy set it.
