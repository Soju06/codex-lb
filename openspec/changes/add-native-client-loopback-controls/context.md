# Context: fixed-endpoint native client routing

## Purpose and scope

This change refines the direction proposed in
[Discussion #1306](https://github.com/Soju06/codex-lb/discussions/1306): a
supported native client stays on one loopback endpoint while `codex-lb` changes
the OAuth identity behind that endpoint. The normative contract is in
[native-client-routing/spec.md](specs/native-client-routing/spec.md) and the
page contract is in
[frontend-architecture/spec.md](specs/frontend-architecture/spec.md).

It covers the server policy, native OAuth admission/relay, internal WebSocket
upstream rotation, and a dedicated dashboard page. Host-side attachment
automation, live attachment inspection, and recovery operations are deferred
to a separate future companion proposal.

## Operator flow

1. On a host-local installation, the operator explicitly enables the feature
   and leaves API-key auth disabled, matching the documented trusted-loopback
   deployment model.
2. The operator attaches Codex App/CLI or a compatible OpenClaw Codex-native
   provider once to `http://127.0.0.1:2455/backend-api/codex`. Static page copy
   may explain that one-time step; this change does not inspect or edit the
   client configuration.
3. The authenticated **Native Routing** page reads the committed policy and
   offers one action: `pool -> client_oauth` or
   `client_oauth -> pool`.
4. A native OAuth HTTP candidate passes bounded classifier/security admission
   and applicable base request validation before it reads the authoritative
   database policy. A downstream native WebSocket applies the same ordering to
   each new turn: base turn validation, then the authoritative read.
5. In `client_oauth`, the request-local bearer/account pair goes only to the
   fixed ChatGPT backend. In `pool`, those two headers are removed before the
   existing account-selection path.
6. The client, downstream WebSocket, selected model, reasoning, tools, and
   requested service tier stay unchanged. Only an upstream WebSocket may be
   replaced at a turn boundary.

The initial provider attachment can require whatever activation the client
normally needs. That one-time fact is separate from steady-state policy
changes, which do not restart Codex, OpenClaw, or `codex-lb`.

## Authentication boundary

The feature does not create a second authentication system. Client OAuth is
admitted only inside the same `api_key_auth_enabled=false` trusted-loopback
model documented in the README, with additional exact native route and
fingerprint checks. No marker, shared secret, API key, or account import is
added.

`sk-clb-*` is reserved before policy classification. Those calls retain the
existing validator and pool/accounting contracts, regardless of the policy.
If a base-valid native OAuth candidate's later authoritative read finds API-key
auth enabled, OAuth cannot substitute for an API key: it fails with
`409 native_client_routing_api_key_auth_incompatible` before any upstream or
pool operation. An existing `client_oauth` policy is not silently coerced when
that setting changes.

A request carrying neither a non-API-key Bearer nor `chatgpt-account-id` is not
an OAuth attempt. Most such traffic keeps the route's existing behavior. The
one safety exception is an otherwise recognized host-local native request on a
tabled route while `client_oauth` is committed: after applicable base request
validation succeeds, the server reads the policy and returns
`409 native_client_oauth_identity_required` rather than silently charging a
pool account. A base-invalid request returns its existing validation error
without a native-policy read. Under `pool`, that same base-valid identity-free
request keeps its existing no-key behavior. Supplying either OAuth identity
field opts into strict classification; an incomplete pair is rejected rather
than silently becoming an uncredentialed pool request.

The native candidate must have one non-API-key Bearer, one bounded account id,
a loopback socket peer and Host, no forwarded-client headers, an exact tabled
method/path, and an allowlisted Codex User-Agent prefix or originator. The
OpenClaw label is useful only after these checks; it is not an authentication
signal.

## Route and identity semantics

The exact route table, byte caps, canonicalization rules, native fingerprint
registry, and request/response header lists are normative in the native routing
spec. The important boundaries are:

- top-level `/v1` stays generic and never sees client OAuth classification;
- the existing `/backend-api/codex/v1/<rest>` path alias is resolved once, but
  encoded separators, dot segments, repeated separators, and invented
  trailing-slash aliases are rejected;
- direct HTTP targets are under `https://chatgpt.com/backend-api`, and the only
  direct WebSocket target is
  `wss://chatgpt.com/backend-api/codex/responses`;
- the existing Codex-base Images adapter normalizes image aliases into a fixed
  `/codex/responses` request, and direct opportunistic admission is local;
- redirects are not followed and arbitrary inbound headers are not copied;
- compatibility normalization may still occur, so this is not byte-for-byte
  passthrough;
- no mode hard-codes or aliases a model, reasoning effort, or service tier.

For every native-policy candidate, the control-flow order is fixed: bounded
classifier/security admission, existing route-specific/base validation,
authoritative policy read, then policy-source routing. A base validation error
does not consult the policy database and therefore cannot be replaced by
`native_client_routing_policy_unavailable`.

For qualifying native OAuth traffic, the native-routing spec is the narrow
conditional authority where unqualified pooled wording in
`responses-api-compat`, `files-upload-protocol`, `images-api-compat`,
`audio-transcriptions-compat`, or `model-catalog-compat` would otherwise
conflict. Validation, payload shaping, error envelopes, bounded telemetry, and
retry-safety tests remain in force. Only policy-source/exact-identity selection,
source/owner-bound continuity, and account-associated logging change in
`client_oauth`. Outside that exact condition, the base capabilities remain
authoritative.

For qualifying OAuth:

- `client_oauth`: direct request-local identity, no pool selection or pooled
  request-log row;
- `pool`: remove client identity, then use existing pool selection,
  compatibility, affinity, logging, and accounting;
- usage: direct signed-in-account usage in `client_oauth`, aggregate existing
  pool usage in `pool`;
- reset-credit consume: direct request identity in `client_oauth`, sanitized
  `409 native_pool_reset_credit_unavailable` in `pool` pending a separate
  durable account-and-credit pinning design.

These mode-specific usage rules do not change existing API-key behavior.

## WebSocket turn example

Assume a downstream Codex WebSocket is connected and turn A starts under
`client_oauth`. While A is streaming, the operator successfully changes policy
to `pool`.

- Turn A completes on the signed-in client identity.
- When turn B's bounded `response.create` arrives, the coordinator retains it
  and runs existing route/protocol-specific base validation. Only if that
  succeeds does it read the database.
- The coordinator closes only the old upstream, binds B to the `pool` policy
  source kind, and uses normal pool selection to establish its upstream.
- If B contains an anchor from turn A, the coordinator removes it before the
  first send only when the existing Responses contract has already prepared a
  retry-safe full-context body. Otherwise B fails before either policy source
  kind sees it.
- If the initially selected pool account fails before `response.created` or
  visible output, existing retry-safe pool rules may select another eligible
  pool account. B remains bound to the `pool` policy source kind; live file pins
  and owner-bound continuations keep their existing fail-closed exceptions.
- The downstream socket and client process remain in place.

If the database read or new upstream setup fails, B is not sent to either
policy source kind and the client receives a sanitized error. Once B is bound
to `pool`, policy changes cannot move it to `client_oauth` mid-turn. Under
`client_oauth`, a turn also keeps its exact request OAuth identity and never
falls back to the pool. Under `pool`, the exact selected account may change only
through existing retry-safe pre-visible failover rules. No replay or failover is
allowed after `response.created` or visible output, and a completed turn cannot
be duplicated.

## Account-scoped continuity and files

`previous_response_id`, supported `input_file.file_id` references, and file
finalize operations are account-scoped. A policy-source-kind switch must not
forward an old response anchor to an incompatible identity. Safe full-context
turns can be rebased before their first send; incremental/tool-output-only turns
receive the retryable
`native_client_routing_continuity_source_changed` error with zero upstream
sends.

Unsupported image-upload references are outside this provenance contract. An
`input_image` containing `file_id` or an `image_url` beginning `sediment://`
retains the base Responses behavior: `400 unsupported_input_image_format`
before authoritative policy read, provenance lookup, policy-source routing, or
upstream I/O. It is never reclassified as
`native_client_routing_file_source_unavailable`.

Successful file registration records only bounded source provenance for the
returned `file_id`. Pool uploads keep the existing exact-account pin. Direct
OAuth uploads store only `client_oauth`, expiry, and the existing routing
metadata needed to find the ephemeral pin; they never store the bearer,
account id, or a token/account-derived fingerprint. Finalize and
Responses/compact calls using supported `input_file.file_id` fail with
`native_client_routing_file_source_unavailable` when that proof is missing,
expired, or belongs to an incompatible policy source kind or pool owner. Because
the proxy intentionally does not retain a direct OAuth account identifier, it
cannot prove that the user did not change signed-in accounts while remaining in
`client_oauth`; upstream may reject that case and the operator must re-upload.

## API-key example

Suppose policy is `client_oauth` and a client sends `Bearer sk-clb-...` to a
protected route. Native policy code does not read the policy or direct that
credential to ChatGPT. The existing API-key dependency decides whether the
request is valid and applies its normal restrictions, pool routing, logging,
and accounting. Changing policy to `pool` produces no API-key behavior change.

## Failure modes

- Capability disabled, non-loopback traffic, forwarded-client headers,
  malformed/duplicate/oversized identity fields, unknown methods/paths, or a
  non-native fingerprint cannot enter the OAuth branch.
- After base validation, API-key auth enabled produces the stable incompatibility
  error for OAuth and never treats OAuth as an API key or trusted no-key request.
- A base-valid recognized native request without its OAuth pair under
  `client_oauth` returns `409 native_client_oauth_identity_required` before pool
  selection.
- An unavailable database or invalid policy row returns
  `503 native_client_routing_policy_unavailable`; no cached or default source
  is substituted after base validation succeeds. A base-invalid request returns
  its existing validation error without consulting native policy.
- Unsafe policy-source-kind changes and owner-bound file/continuity mismatches
  return the stable continuity or file-source error before the request reaches
  an incompatible identity.
- An upstream redirect returns
  `502 native_oauth_upstream_redirect_rejected`; no destination is followed.
- Direct network/upstream failure is sanitized and never crosses to a pool
  account or different OAuth identity.
- Size-limit failures terminate the direct operation without exposing bearer
  or account values.

## Page and future extension boundary

The page is an independent lazy feature with a prominent policy action,
compatibility state, flow explanation, and static attachment instructions. It
does not know whether local client files currently match the instructions and
does not offer repair, rollback, backup, or process-control buttons.

The page warns that account-scoped response anchors, supported
`input_file.file_id` references, and file finalize operations do not
automatically move between policy source kinds or exact pool owners. It explains
that unsafe continuation fails closed, supported input-file work may require
re-upload after a switch/restart/expiry, and `codex-lb` cannot verify
same-account continuity in direct mode without persisting identity. It also
distinguishes the base `400 unsupported_input_image_format` rejection for
image-upload references, which occurs without a policy read, from the new
provenance errors. The warning is static; the page does not inspect files or
claim that a live workflow is safe to switch.

A later proposal may define a companion contract for those host operations.
That future work is not needed for the core policy or relay and cannot be
silently added to this change.

## Evidence boundary

The dashboard proves only the committed policy, feature capability, and whether
API-key auth is compatible with OAuth switching. It does not prove that a live
turn completed, which account's quota changed, or what service tier upstream
actually granted. Requested Fast is only a client request signal.

Bounded E2E may prove route selection using account-free direct records with no
pool account association during direct windows and selected-account records
during pool windows. Quota or actual-tier claims remain unresolved unless an
independent live measurement establishes them.

The proposal recommends the stable page signature `DOMANHDUC · dmdfami`, but
maintainers explicitly approve or decline it. Functional acceptance does not
quietly depend on that product decision.
