## Context

Reasoning effort is a client-visible choice with two different operator
controls today: no control, or `enforcedReasoningEffort`, which overwrites the
choice. The latter is useful for a fixed low-cost key but is too coarse for a
corporate key where users may choose `minimal`, `low`, `medium`, `high`, or
`xhigh` and must not select `max` or `ultra`.

The proxy already normalizes model aliases into `reasoning.effort`, rewrites
unsupported and client-plane wire aliases only immediately before forwarding,
and uses one request-policy module across Responses, compact, and WebSocket
paths. Chat Completions converts to that Responses form before selection; the
source-routed chat path additionally retains a chat-shaped outbound payload.

## Goals / Non-Goals

**Goals:**

- Let an operator persist a per-key, explicit set of client-selectable efforts.
- Preserve existing keys and requests when no policy is configured.
- Reject forbidden work before it consumes quota or reaches an upstream.
- Give dashboard users a clear mutually-exclusive choice between forcing one
  effort and allowing a set.
- Keep all proxy route families consistent, including aliases and source chat.

**Non-Goals:**

- Infer or change an upstream model's default effort when the client omits an
  effort.
- Rank efforts or introduce a generic numeric "maximum effort" setting.
- Change the existing `ultra` to `max` wire alias, model catalog, pricing, or
  Fast Mode policy.
- Add global settings, per-model policy, roles, or new dashboard navigation.

## Decisions

### Persist an optional explicit allowlist, not a maximum

Store `allowed_reasoning_efforts` as nullable JSON text alongside the existing
nullable `allowed_models` field. The dashboard API exposes camelCase
`allowedReasoningEfforts`. `null` means no restriction; a supplied list must be
non-empty and contain only canonical client-plane efforts (`minimal`, `low`,
`medium`, `high`, `xhigh`, `max`, `ultra`). Service normalization trims,
lowercases, de-duplicates, and orders values by the catalog's canonical order.

An explicit list avoids assuming that future effort names are linearly ordered
or that every model supports the same scale. It also expresses the requested
`minimal` through `xhigh` policy exactly.

### Keep fixed and selectable policies mutually exclusive

An API key may have either `enforcedReasoningEffort` or
`allowedReasoningEfforts`, never both. The service validates the effective
state on create and patch, so a partial PATCH cannot accidentally preserve the
other policy. The dashboard clears the opposite control before submission.

Combining them is not useful: enforcement always replaces the client request,
making an allowlist invisible. Rejecting the ambiguous state is clearer than
inventing precedence or silently accepting dead configuration.

### Validate the client-plane effort before wire normalization

The shared policy derives the client-plane effort before it mutates the
request: an accepted Cursor alias such as `gpt-5.6-sol-xhigh` is checked as
`xhigh`; otherwise it checks explicit `reasoning.effort`. Only afterwards do
current compatibility transforms run, including `minimal` fallback and
`ultra` to `max` wire aliasing. `xhigh` is likewise lowered to upstream
`high` by accepted model aliases. Authorization remains client-plane and
exact: `xhigh` and `high`, as well as `ultra` and `max`, are separate policy
choices even when their downstream wire forms coincide.

The policy therefore describes what the client chose rather than an internal
wire representation. A request with no explicit effort remains valid and uses
the existing upstream default; inventing a default would be a separate
behavioural feature and would risk breaking clients.

### Fail visibly and before side effects

A forbidden effort raises a typed proxy permission exception with HTTP `403`,
OpenAI error type `permission_error`, code `reasoning_effort_not_allowed`, and
parameter `reasoning.effort`. It records only low-cardinality key id and
effort in the diagnostic log. The check runs before admission and API-key
quota reservation, model-source dispatch, account selection, or upstream I/O.

This is deliberately a rejection, not a downgrade: silently changing a
developer's requested reasoning depth hides an operator policy and makes
unexpected output quality difficult to diagnose.

### Use the converted Responses payload as the single enforcement point

Responses, compact, and WebSocket paths already call the shared enforcement
function. Chat Completions converts to `ResponsesRequest` before selection,
so the same call rejects disallowed effort before either account or source
routing. Source-routed chat also resolves `ultra` to the `max` wire value for
every accepted chat reasoning spelling (`reasoning_effort`, `reasoningEffort`,
`reasoning.effort`, and `thinking`) after the request passes the allowlist.
When a client supplies conflicting spellings, the outbound values are aligned
to the already-authorized client-plane effort so a source cannot select a
disallowed value from an ignored alias. Other accepted client-plane values,
including `minimal` and model-alias-derived `xhigh`, remain unchanged for an
external source that may support them directly.

Source-routed Responses traffic likewise normalizes all accepted reasoning
aliases before egress. The canonical `reasoning.effort` wins when aliases
conflict, and the aliases are removed so an external source cannot select a
different, unauthorized value.

The origin route records that policy has already been applied when it forwards
the signed request to an owner instance. The owner still authenticates the
internal request and validates model access, but does not re-authorize or
re-normalize the reasoning effort. This keeps the policy idempotent across the
HTTP bridge and prevents a client-plane alias such as `xhigh` from being
mistaken for its wire value `high` on the second pass.

The request model keeps the original client-plane effort in a private field
while enforcement mutates the payload. This is intentionally not serialized:
the signed bridge request is trusted only after the origin has completed policy
enforcement, and the owner receives an explicit internal call-site marker.

## Risks / Trade-offs

- **Client omits an effort and an upstream default changes**: the request
  remains compatible, but the allowlist cannot cap an unexpressed upstream
  default. A future "required/default reasoning effort" policy would need its
  own explicit contract rather than changing this feature's meaning.
- **A model alias hides an effort**: the policy derives the client-plane
  effort from accepted model aliases before they are normalized for upstream,
  so an `xhigh` suffix cannot bypass the policy. Source-routed chat traffic
  applies the same `ultra` to `max` wire conversion as Responses traffic only
  after the exact client-plane policy check.
- **Malformed manually-edited stored JSON**: service deserialization treats it
  as an empty restrictive policy, so explicit effort requests fail rather than
  silently becoming unrestricted. Normal dashboard/API writes cannot create
  that state.
- **Operators switch policies through a partial PATCH**: effective-state
  validation rejects a key that would hold both settings; the dashboard sends
  the clearing value in the same request.
- **Mixed-version replicas during a rolling upgrade**: this feature does not
  add protocol machinery for mixed application versions. Operators running a
  multi-replica deployment must complete the application rollout before
  creating or changing reasoning allowlists.

## Migration Plan

The migration adds one nullable allowlist column. Existing rows retain current
unrestricted behavior because their allowlist remains `NULL`. The database
rejects rows that combine a fixed effort with an allowlist. Rolling back drops
the constraint and column without changing key identity or other stored data.

## Open Questions

None.
