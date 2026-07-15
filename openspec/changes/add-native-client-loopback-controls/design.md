# Design: fixed-endpoint native client routing

This design is the narrowed follow-up to
[Discussion #1306](https://github.com/Soju06/codex-lb/discussions/1306).

## Decisions

### 0. Host-local support is an explicit capability

`CODEX_LB_NATIVE_ROUTING_ENABLED=true` is required for the dashboard feature,
policy API, and OAuth data plane. Missing, false, or invalid values fail closed.
The OAuth data plane also requires the actual socket peer and HTTP Host to be
loopback, rejects forwarded-client headers, and accepts only an exact native
method/path and Codex fingerprint. Browser platform and hostname inference are
not security controls.

This is not a new client-authentication scheme. It inherits the repository's
documented no-key trusted-loopback contract and therefore operates only while
`api_key_auth_enabled=false`. After classifier/security admission and applicable
base validation succeed, an OAuth candidate whose authoritative read finds
API-key auth enabled gets HTTP `409` with code
`native_client_routing_api_key_auth_incompatible` before upstream or pool I/O.
A WebSocket handshake uses the same denial response. If the setting changes
while a downstream socket is open, its next turn receives the same stable code
and is not forwarded.

### 1. The persisted database row is authoritative

The singleton settings row stores non-null `pool | client_oauth`, defaulting to
`pool`, and its existing monotonically increasing settings version is the
policy revision. The authenticated policy write commits the new value and
revision before returning success. A same-value write may avoid an update but
returns the complete currently committed projection.

The native OAuth data plane deliberately bypasses settings caches, but only
after a candidate has passed bounded native classification/security admission
and every applicable route-specific/base request validation. It then reads
`mode`, settings version, and `api_key_auth_enabled` from the authoritative
database in one query for every admitted, base-valid OAuth HTTP request, every
admitted, base-valid OAuth WebSocket handshake, every admitted, base-valid
recognized identity-free native safety check, and every base-valid new
WebSocket turn. Processing is therefore classifier/security
admission -> base validation -> authoritative policy read -> policy routing.
Other identity-free traffic remains outside the policy, and a candidate rejected
before the policy stage performs no native-policy read. The read is the request
or turn's policy linearization point. Thus a read that starts after a successful
write response observes that write or a later one; a read completed before the
write may finish on its original snapshot.

For a base-valid candidate that reaches the policy stage, an unavailable
database, invalid stored mode, or incomplete row returns `503`
`native_client_routing_policy_unavailable`; it never falls back to a cached
value, `pool`, or `client_oauth`.

### 2. API keys are outside the policy

The classifier first reserves `sk-clb-*` Bearers for the existing API-key
path. Such requests do not read the native policy and are handed unchanged to
the existing validator, model/tier restrictions, pool selection, request
logging, rate limits, and accounting. This remains true under both policy
values and preserves the existing behavior when API-key auth is disabled or
enabled.

Only a single non-`sk-clb-*` Bearer plus a single bounded
`chatgpt-account-id` can become a native OAuth candidate. The candidate first
passes the same trusted-loopback boundary already documented by the project plus
the stricter route and fingerprint checks in this change, then applicable base
validation. The later authoritative read enforces that API-key auth is disabled
before policy routing. The identity is not validated by importing the account
into the pool, and no new secret or marker header is introduced.

A request with neither a non-API-key Bearer nor `chatgpt-account-id` is not an
OAuth attempt. It normally retains the route's existing no-key/API-key
behavior. To prevent an operator-selected `client_oauth` mode from silently
charging the pool, an identity-free request that still satisfies the bounded
loopback, tabled-route, and native-fingerprint checks first runs applicable base
validation and reads the authoritative policy only after that succeeds. It
keeps existing behavior in `pool`, but returns HTTP `409`
`native_client_oauth_identity_required` before pool I/O in `client_oauth`.
Untabled, non-native, remote, capability-disabled, and generic `/v1` traffic
does not gain a policy read. Once either OAuth identity field is present,
incomplete or ambiguous identity fails closed instead of being downgraded into
an uncredentialed pool request.

In `pool`, a qualifying OAuth identity is removed before existing local no-key
pool processing. In `client_oauth`, it is used only by the direct relay.
Malformed, ambiguous, remote, forwarded, unknown-route, or non-native OAuth
candidates fail before pool selection.

### 3. Classification is finite and byte-bounded

The normative method/path table and header lists live in
`specs/native-client-routing/spec.md`. OAuth-specific parsing adds only these
fixed bounds: one visible-ASCII OAuth token of 1..16384 bytes excluding comma
and whitespace; a complete Authorization field of at most 16391 bytes; one
`chatgpt-account-id` of 1..256 ASCII bytes matching `[A-Za-z0-9._:-]+`; at most
16 KiB for every other inspected or forwarded header value; raw path at most
2048 bytes; and raw query at most 8192 bytes.

The relay reuses rather than narrows the route's existing payload budgets:
Responses HTTP uses `max_decompressed_responses_body_bytes` (default 128 MiB),
other HTTP routes use `max_decompressed_body_bytes` (default 32 MiB), and
client-to-proxy Responses WebSocket ingress uses `--ws-max-size` /
`UVICORN_WS_MAX_SIZE` (default 128 MiB). Existing request models and upstream
transport budgets may impose their existing lower limits or compatibility
handling. Any limit violation is rejected without policy fallback, and
credentials are excluded from error text.

Canonicalization uses the ASGI decoded path and raw path together. The raw path
must be ASCII and represent the decoded path exactly; encoded separators,
backslashes, dot segments, duplicate separators, invalid percent escapes, and
raw/decoded disagreement are rejected. The existing
`/backend-api/codex/v1/<non-empty-rest>` alias is collapsed exactly once before
table lookup. Only the two already registered `/api/codex/.../` trailing-slash
forms lose one terminal slash; other trailing slashes are not OAuth aliases.
Top-level `/v1/<rest>` never enters this classifier.

Native identity uses the exact shared Codex registry:

- User-Agent prefix, case-insensitive after surrounding-space removal:
  `codex_cli_rs`, `codex-tui`, `codex_exec`, `codex_sdk_ts`, `codex_vscode`,
  `Codex Desktop`, or `Codex `;
- originator, exact and case-sensitive after surrounding-space removal:
  `Codex Desktop`, `codex_atlas`, `codex_chatgpt_desktop`, `codex_cli_rs`,
  `codex_exec`, `codex_sdk_ts`, or `codex_vscode`.

At least one registry check must pass, and every supplied fingerprint identity
field must be unambiguous. Continuity headers do not authenticate a client.

### 4. Client OAuth uses a fixed-origin native relay

HTTP egress is fixed to `https://chatgpt.com/backend-api`; WebSocket egress is
fixed to `wss://chatgpt.com/backend-api/codex/responses`. The exact route table
also identifies the two local compatibility actions: OAuth images keep the
existing Codex-base Images adapter before its normalized `/codex/responses`
egress, and direct OAuth opportunistic admission returns local admitted state
instead of inventing an upstream endpoint. The direct branch does not use
operator-supplied upstream origins, model sources, owner forwarding, or pool
proxy selection. Redirect following is disabled; any upstream 3xx becomes
`502 native_oauth_upstream_redirect_rejected` without forwarding `Location`.

The relay rebuilds `Authorization`, `chatgpt-account-id`, Host, and body framing
and forwards only the explicitly listed native headers. It never forwards
Cookie, proxy credentials, forwarding headers, connection-nominated headers,
or arbitrary `x-*` fields. The outbound transport has no cookie jar, suppresses
automatic User-Agent/Cookie headers, requests `Accept-Encoding: identity`,
disables automatic decompression and redirects, and keeps normal TLS
verification enabled. Response headers are independently allowlisted, bounded
to 16 KiB each, and never include cookies. Response bodies stream in the
existing bounded chunks without whole-response buffering.

Every direct OAuth WebSocket handshake reuses the existing Responses handshake
normalization after inbound filtering: remove the incompatible
`responses=experimental` token and ensure exactly one case-insensitive
`responses_websockets=2026-02-06` token in `openai-beta`. Merely allowlisting an
inbound `openai-beta` value is not a substitute for this synthesis.

After classifier/security admission, applicable base validation, and the
authoritative policy read selects `client_oauth`, the direct branch occurs
before model-source selection, pooled admission, affinity/account selection,
reservation, owner forwarding, conversation archive, and pooled request-log
creation. Existing native compatibility parsing may normalize the
request/response contract, but it does not choose or override model, reasoning
effort, tools, or requested service tier on native Responses paths. The existing
Images adapter retains its documented image-model validation and host-Responses
translation; the routing policy and page add no model choice. Direct failures
never retry through a pool account or another OAuth identity.

Credentials live only in an immutable request/downstream-socket identity whose
representation omits them and in rebuilt outbound headers. They never enter
database rows, caches, archives, request logs, metrics labels, exceptions,
browser responses, or application logs.

### 5. Native routing is a narrow conditional compatibility override

For a qualifying tabled request only, `client_oauth` is more specific than
unqualified pooled wording in the existing compatibility capabilities:

Every applicable base request validator runs before the authoritative native
policy read. A base rejection is returned without consulting policy or entering
any mode-specific routing branch.

- `responses-api-compat`: validation, contract shaping, streaming, bounded
  retry-safe replay, public error masking, and the
  `400 unsupported_input_image_format` rejection remain; pool account
  selection, owner forwarding, archive ownership, and cross-policy-source
  replay do not.
- `files-upload-protocol`: payload limits, polling, errors, and synthetic-model
  accounting remain; selected-pool-account refresh/retry is replaced by the
  request-local identity and source-provenance rules below.
- `images-api-compat`: image validation, host-Responses translation, response
  shape, public model accounting, and bounded route telemetry remain; pooled
  account/sticky/retry selection is replaced by the fixed direct relay.
- `audio-transcriptions-compat`: multipart validation, effective-model policy,
  budgets, and error envelopes remain; selected-account refresh/retry is
  replaced by the request-local identity without pool fallback.
- `model-catalog-compat`: `client_oauth` returns the fixed upstream's catalog for
  that request identity; pooled `/backend-api/codex/models` and every top-level
  `/v1/models` request retain the existing bootstrap/refreshed merged catalog.

Base route-completion telemetry and request-log records that do not require a
selected pool account remain required. A direct record carries no account id,
API-key id, bearer-derived value, or account-derived fingerprint. "No pooled
request-log creation" means no selected-account association, not suppression of
safe compatibility telemetry. Outside the qualifying `client_oauth` branch,
all base capability requirements keep their original authority.

### 6. Account-scoped anchors and supported files cannot cross policy sources or owners

Policy source kind (`pool` or `client_oauth`) and exact credential identity are
separate concepts. Exact identity is the request/downstream-socket OAuth pair in
`client_oauth` and the selected pool account in `pool`. When a turn changes
policy source kind, a `previous_response_id` from the prior kind is stale before
first send. The coordinator may rebase to the already-prepared no-anchor body
only when the existing Responses rules classify the body as retry-safe and
self-contained. Otherwise it sends the turn to zero upstreams and returns
retryable `native_client_routing_continuity_source_changed`. It never strips an
anchor from incremental tool output or supported `input_file.file_id` content
whose owner/source constraints do not permit a fresh turn.

Successful file registration records bounded, expiring provenance for its
`file_id`. Pool mode keeps the existing exact account-owner pin. Direct mode
records only the literal `client_oauth` source kind, expiry, and existing
ephemeral routing metadata; it stores no bearer, account id, or value derived
from either. Finalize and supported `input_file.file_id` operations require live
provenance compatible with the policy source kind and, for pool pins, the exact
owner account. Missing, expired, or cross-source/cross-owner provenance returns
HTTP `409`
`native_client_routing_file_source_unavailable` before forwarding. The proxy
cannot prove that two direct requests use the same signed-in account without
persisting identity, so a same-mode external account change remains upstream-
validated and may require re-upload.

Every `input_image.file_id` and `input_image.image_url` beginning `sediment://`
is explicitly outside this provenance path. The base Responses validator runs
before the authoritative native-policy read and returns
`400 unsupported_input_image_format`; the native-routing layer does not turn an
unsupported image reference into a `409` provenance result.

### 7. WebSockets bind a turn to one policy source kind and preserve safe replay

One coordinator owns the long-lived downstream WebSocket. It keeps the current
upstream source and the bounded protocol setup state required by the existing
client-facing contract. A turn follows this sequence:

1. accept and retain one `response.create` frame that already passed the
   existing configurable downstream WebSocket ingress budget;
2. apply every existing route/protocol-specific base validation and, on
   rejection, return its stable error without a native-policy read or upstream
   send;
3. read the authoritative database snapshot for the base-valid turn;
4. if its mode selects a policy source kind different from the bound upstream,
   finish any active turn, close only that upstream, and establish a fresh
   upstream for the new policy source kind; a revision-only change with the
   same mode updates the bound snapshot without replacing a healthy upstream
   solely for policy;
5. validate account-scoped anchors and prepare a permitted no-anchor rebase or
   fail before send;
6. apply the current bounded setup state, including the existing WebSocket beta
   normalization, and send the prepared initial body under the bound policy
   source kind.

The downstream socket is not closed for a normal policy change, and neither
the client nor service restarts. Each accepted downstream create binds to one
policy source kind, which cannot change because policy changed or an upstream
failed. Under `client_oauth`, the exact request/downstream-socket OAuth identity
also remains fixed and the turn never falls back to the pool. Under `pool`, the
existing Responses contracts retain exact-account selection and retry behavior,
including retry-safe pre-visible failover from one eligible account to another
where no live file pin or previous-response owner constraint requires
fail-closed handling. A pool account replacement does not change the `pool`
policy source kind.

Any permitted replay or pool-account failover occurs only before
`response.created` or visible output and does not re-read policy. Once either is
visible, the turn is never replayed or failed over, so a completed turn cannot
be duplicated. If the policy read, continuity preparation, or all setup allowed
within the bound policy source kind fails, the retained frame is sent to zero
upstreams and the downstream receives the stable sanitized error. The existing
one-active-turn protocol rule handles overlapping creates; this feature does not
add an unbounded turn queue.

A policy update during an active turn does not move that turn. The next create
performs a new database read after the terminal event and uses the then-current
revision.

### 8. Usage and reset credits remain mode-specific for OAuth only

For qualifying OAuth traffic, `client_oauth` relays usage and reset-credit
consume with that request's identity. In `pool`, usage follows the existing
aggregate pool path after client identity removal. Pool reset-credit consume
returns sanitized `409 native_pool_reset_credit_unavailable` before account
selection until a separate design provides durable account-and-credit pinning.

These rules do not alter the existing `sk-clb-*` usage or reset-credit paths.

### 9. OpenClaw support means Codex-native protocol support

OpenClaw is supported only when its selected provider targets the fixed native
endpoint and emits an allowlisted Codex User-Agent prefix or originator. A
case-insensitive suffix token matching
`(?:^|[\s(;])openclaw(?:/|[;\s)]|$)` may classify attribution as OpenClaw after
native admission succeeds. That token alone never grants admission, supplies
OAuth identity, or turns a generic `/v1` provider into a native client.

### 10. The page controls policy only

The dedicated page loads capability and policy, shows one state-aware action,
and explains the fixed route. It may show static one-time instructions for
attaching Codex and a compatible OpenClaw provider. It does not inspect local
files, report live attachment health, execute recovery, restart processes, or
contact host-side automation.

The page also gives a static fail-closed warning: a recognized native request
without its OAuth pair cannot use `client_oauth`; response anchors, supported
`input_file.file_id` references, and file finalize operations do not migrate
between policy source kinds or exact owners; and supported input-file work may
need to be uploaded again after a switch, restart, provenance expiry, or
external signed-in-account change. It separately states that unsupported
`input_image` file references receive the base `400` rejection rather than a
provenance `409`. It does not inspect or enumerate live files.

Attachment automation and recovery are deferred to a separately reviewable
future companion contract. They are not API, schema, task, or release
dependencies of this proposal.

### 11. Claims and attribution remain explicit decisions

Policy state proves configured routing only. Requested Fast and actual upstream
tier are distinct, and config state alone proves neither completed inference
nor quota attribution. Any account/quota statement requires bounded live
evidence.

The proposal recommends `DOMANHDUC · dmdfami` unchanged in every locale, but
maintainers decide whether it ships. Acceptance or rejection is recorded before
frontend implementation; the functional feature does not depend on approval.

## Non-goals

- Reading, copying, replacing, exporting, or displaying client auth stores
- Adding a client secret, marker header, model alias, or model allowlist
- Applying the policy to API keys, top-level `/v1`, remote clients, or generic
  SDK traffic
- Following redirects or using configurable origins for direct OAuth egress
- Changing policy source kind during an in-flight HTTP response or WS turn, or
  changing the exact OAuth identity of a `client_oauth` turn
- Restarting Codex, OpenClaw, or `codex-lb`
- Running quota-consuming verification from the routine page control
- Shipping attachment status, recovery operations, or host automation here
