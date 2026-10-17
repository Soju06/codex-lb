# Context: canonical reasoning state for Responses Lite

## Purpose and scope

Responses Lite is a coupled wire contract: its `additional_tools` request shape,
canonical transport signal, and reasoning state must agree. This change repairs
the missing reasoning half at the final upstream boundary. The normative
contract is in
[`specs/responses-api-compat/spec.md`](./specs/responses-api-compat/spec.md).

## Decision rationale and alternatives

The proxy repairs older or inconsistent client payloads because it already owns
the derived Lite signal. Rejecting the request would continue the production
failure, and suppressing the signal would forward a body whose Lite tool bundle
no longer has the semantics the client requested.

Normalization is conditional on the final canonical signal. It is not a general
reasoning-schema restriction and does not make an inbound header or marker
trusted. This keeps non-Lite clients and future reasoning extensions compatible.

## Constraints and failure modes

- The exact wire value is the JSON string `"all_turns"`; case variants and
  non-string values are not equivalent.
- Reasoning effort, summary, and extension fields must survive the repair.
- Bridge trimming and marker-only incremental frames may no longer contain the
  original `additional_tools` prefix, so the already-established trusted Lite
  disposition must reach final serialization.
- Fresh replays can deliberately sever Lite linkage. Mutating only final wire
  dictionaries prevents an earlier send from contaminating that rebuilt body.
- The added field must be present before whole-payload websocket sizing and
  final compact serialization. Compact's existing input-only budget remains
  unchanged because reasoning is outside the measured input array.

## Concrete examples

A Lite request from an older client can arrive as:

```json
{"reasoning":{"effort":"max","summary":"auto","vendor_hint":7}}
```

When the proxy emits the canonical Lite header or marker, the final upstream
body becomes:

```json
{"reasoning":{"effort":"max","summary":"auto","vendor_hint":7,"context":"all_turns"}}
```

The same body on a non-Lite request is not changed by this feature. A stale
inbound Lite header or untrusted websocket marker is stripped according to the
existing contract and does not activate normalization.

## Operational notes

No rollout setting or data migration is needed. Verification should pair the
serialized body assertion with the canonical header/marker assertion so future
transport changes cannot reintroduce an inconsistent signal. The implementation
PR should close issue #1411 and monitor upstream invalid-request counts after
deployment.
