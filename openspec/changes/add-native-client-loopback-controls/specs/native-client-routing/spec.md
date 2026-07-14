## ADDED Requirements

### Requirement: Explicit host-local capability and auth-mode gate

Native-client routing MUST be disabled by default. Only the literal,
case-insensitive value `true` for `CODEX_LB_NATIVE_ROUTING_ENABLED` SHALL enable
the authenticated dashboard capability, policy API, and native OAuth data
plane. Missing, empty, false, or invalid values MUST fail closed. The capability
endpoint SHALL be authenticated `GET /api/native-client-routing/capability`,
and its response SHALL contain exactly `{ "enabled": boolean }`.

Native OAuth routing SHALL be compatible only with the existing documented
trusted-loopback mode where `api_key_auth_enabled=false`. After native
classification/security admission and applicable route/base validation succeed,
a native OAuth HTTP request or WebSocket handshake whose authoritative read
finds API-key auth enabled MUST fail before upstream or pool I/O with HTTP `409`
and code
`native_client_routing_api_key_auth_incompatible`. A base-valid qualifying OAuth
turn on an already accepted downstream WebSocket MUST receive that same code
before its create frame is forwarded. The feature MUST NOT introduce a client
secret, marker header, or alternative trust mechanism.

#### Scenario: Generic installation uses the safe default

- **WHEN** the opt-in is absent or not true
- **THEN** authenticated capability reports disabled
- **AND** the policy API and OAuth data plane cannot activate

#### Scenario: API-key auth is enabled

- **GIVEN** a non-API-key native OAuth candidate passes native security admission
  and route-specific/base validation on a tabled route
- **WHEN** its authoritative read reports API-key auth enabled
- **THEN** it receives `409 native_client_routing_api_key_auth_incompatible`
- **AND** no client identity is sent upstream or into pool selection

### Requirement: Persisted authoritative credential-source policy

The server SHALL persist exactly `pool` or `client_oauth` in the singleton
dashboard settings row, defaulting existing and new installations to `pool`.
The field SHALL participate in the row's existing optimistic versioning, and
that committed settings version SHALL be the policy revision. Invalid or
missing stored values MUST NOT be coerced to either mode.

A successful policy mutation MUST return only after its database transaction
commits. For native-policy candidates, processing MUST occur in this order:

1. reserve `sk-clb-*` for the existing API-key path, then perform bounded native
   classification and security admission;
2. run every applicable existing route-specific/base request validation;
3. only after that validation succeeds, read `mode`, revision, and
   `api_key_auth_enabled` from the authoritative database in one operation; and
4. apply policy-source routing, continuity/provenance checks, and upstream or
   pool selection.

The native OAuth data plane MUST bypass settings caches for that read. It MUST
perform the read for every admitted, base-valid OAuth HTTP request, every
admitted, base-valid recognized identity-free native safety check defined
below, and after base validation of every new WebSocket turn. The authoritative
read SHALL be the request or turn's policy linearization point. A request or
turn rejected by classification, security admission, or route-specific/base
validation MUST NOT read native policy. A data-plane read that begins after a
successful mutation response MUST observe that revision or a later revision. A
database read failure, invalid mode, or incomplete row MUST return `503`
`native_client_routing_policy_unavailable` without using a cached or default
source.

#### Scenario: Existing installation migrates

- **WHEN** the migration adds the credential-source field
- **THEN** its value is non-null `pool`
- **AND** all unrelated settings remain unchanged

#### Scenario: Successful write is visible to a later turn

- **WHEN** a valid policy write returns success before a new turn starts its
  authoritative read
- **THEN** that turn observes the committed revision or a later one
- **AND** it cannot use an older cached policy

#### Scenario: Authoritative policy cannot be read

- **GIVEN** native classification/security admission and route-specific/base
  validation succeeded
- **WHEN** the database read fails or returns an invalid policy row
- **THEN** the OAuth request or new turn receives
  `503 native_client_routing_policy_unavailable`
- **AND** it is sent to neither the pool nor the direct OAuth upstream

#### Scenario: Base-invalid native request never reads policy

- **GIVEN** a tabled native OAuth or recognized identity-free candidate passes
  bounded classification and security admission
- **WHEN** its existing route-specific/base validator rejects the request
- **THEN** the validator's stable error is returned without an authoritative
  native-policy database read
- **AND** neither policy-source routing nor upstream/pool selection runs

### Requirement: Authenticated strict policy API

The backend SHALL expose authenticated `GET` and same-origin `PUT` operations at
`/api/native-client-routing/policy`. Reads SHALL remain available to
read-only dashboard sessions. Writes MUST require dashboard write permission,
exact `application/json`, a matching Origin, `Sec-Fetch-Site: same-origin`, and
a strict body containing only `mode`.

Every successful read/write response SHALL contain exactly `mode`, `revision`,
`app_restart_required`, `oauth_switching_available`, and
`incompatibility_code`. `app_restart_required` MUST be `false`.
`oauth_switching_available` MUST equal `!api_key_auth_enabled`, and
`incompatibility_code` MUST be null when available or
`native_client_routing_api_key_auth_incompatible` otherwise. A write that
would set `client_oauth` while unavailable MUST return the stable `409` without
mutation. A same-value compatible update MAY avoid a write but MUST return the
complete committed projection.

#### Scenario: Read-only policy inspection

- **WHEN** a read-only dashboard session requests policy
- **THEN** it receives the strict committed projection
- **AND** it cannot submit a policy mutation

#### Scenario: Incompatible client-OAuth activation

- **WHEN** a writer requests `client_oauth` while API-key auth is enabled
- **THEN** the API returns
  `409 native_client_routing_api_key_auth_incompatible`
- **AND** the committed mode and revision remain unchanged

#### Scenario: Cross-origin or malformed update

- **WHEN** content type, Origin, Fetch Metadata, body shape, or mode is invalid
- **THEN** the request is rejected before settings mutation
- **AND** the existing policy remains unchanged

### Requirement: API-key requests are policy-independent

A request whose single Bearer token starts with the exact `sk-clb-` prefix MUST
be handed unchanged to the route's existing API-key dependency and normal
request path before native OAuth policy classification. It MUST NOT read the
native policy, be reinterpreted as ChatGPT OAuth, have its Authorization header
removed by this feature, or receive native-policy mode-specific behavior.

Existing validation, disabled-auth behavior, model/tier restrictions, pool
selection, rate limits, request logging, usage/reset-credit semantics, and
accounting SHALL remain authoritative for `sk-clb-*` under both policy values.

#### Scenario: API-key client calls a protected native route

- **WHEN** a client supplies `Bearer sk-clb-...` while either policy is stored
- **THEN** the existing API-key dependency and route logic decide the result
- **AND** native OAuth policy is not read or applied

#### Scenario: API-key client calls usage

- **WHEN** a `sk-clb-*` client calls an existing usage or reset-credit surface
- **THEN** its existing API-key contract remains unchanged
- **AND** it does not receive OAuth pool-mode substitution behavior

### Requirement: Bounded unambiguous native OAuth classification

Native OAuth classification SHALL remain bounded and unambiguous. After the
`sk-clb-*` reservation requirement has handed API-key requests to their
existing path, the feature SHALL treat a remaining request as an OAuth attempt
when it carries either a non-API-key Bearer or
`chatgpt-account-id`. Once an OAuth attempt exists, classification MUST require
all of the following:

- one non-`sk-clb-*` Bearer token whose token is 1..16384 visible-ASCII bytes
  excluding comma and whitespace;
- one `chatgpt-account-id` of 1..256 ASCII bytes matching
  `[A-Za-z0-9._:-]+` with no surrounding whitespace;
- the complete Authorization field is at most 16391 bytes (`Bearer ` plus the
  maximum token), and every other header value inspected or forwarded by this
  feature is at most 16 KiB;
- an actual IPv4/IPv6 loopback socket peer and exactly one Host resolving
  syntactically to `localhost`, `127.0.0.0/8`, or `::1`, with at most one valid
  port;
- no `Forwarded`, any `X-Forwarded-*`, `X-Real-IP`, `True-Client-IP`, or
  `CF-Connecting-IP` header;
- an exact tabled method and canonical path; and
- an allowlisted Codex-protocol User-Agent prefix or originator.

The shared User-Agent registry SHALL match, case-insensitively after trimming,
only values beginning `codex_cli_rs`, `codex-tui`, `codex_exec`,
`codex_sdk_ts`, `codex_vscode`, `Codex Desktop`, or `Codex `. The shared
originator registry SHALL match, case-sensitively after trimming, only
`Codex Desktop`, `codex_atlas`, `codex_chatgpt_desktop`, `codex_cli_rs`,
`codex_exec`, `codex_sdk_ts`, or `codex_vscode`. If both fingerprint fields are
present, both MUST be valid; duplicate fingerprint or identity headers MUST be
rejected. `x-codex-*`, session, and continuity headers MUST NOT authenticate a
client.

Malformed, ambiguous, oversized, forwarded, remote, or non-native OAuth
candidates MUST fail before pool selection and MUST NOT disclose the bearer or
account value in the response.

A remaining request with neither OAuth identity field SHALL be a recognized
identity-free native candidate only when the capability is enabled and it
satisfies every non-identity bound above: bounded inspected headers, loopback
peer and Host, no forwarded-client header, an exact tabled method/path, and an
allowlisted Codex fingerprint. That candidate MUST first run the route's
applicable existing/base request validation. Only if validation succeeds MUST
it read the authoritative policy, without bypassing any existing API-key-auth
denial. While API-key auth is disabled, `pool` MUST retain the route's existing
no-key behavior, but `client_oauth` MUST return HTTP `409` with code
`native_client_oauth_identity_required` before pool selection or upstream I/O.
Identity-free traffic that is untabled, non-native, remote, forwarded,
capability-disabled, or generic top-level `/v1` MUST retain existing behavior
without a native-policy read.

#### Scenario: Qualifying local Codex request

- **WHEN** a tabled request satisfies every loopback, identity, size, and
  fingerprint condition and passes route-specific/base validation while API-key
  auth is disabled
- **THEN** it is classified as request-local native OAuth
- **AND** only then does the authoritative persisted policy choose its OAuth
  source behavior

#### Scenario: Identity-free native request fails closed in client-OAuth mode

- **WHEN** an otherwise recognized, base-valid tabled native request has neither
  a non-API-key Bearer nor `chatgpt-account-id` while `client_oauth` is committed
  and API-key auth is disabled
- **THEN** it receives `409 native_client_oauth_identity_required`
- **AND** it is sent to neither a pool account nor the direct upstream

#### Scenario: Identity-free native request keeps pool behavior

- **WHEN** the same recognized, base-valid identity-free native request observes
  `pool` while API-key auth is disabled
- **THEN** its existing trusted-loopback no-key route behavior remains in force
- **AND** the policy does not supply or invent an OAuth identity

#### Scenario: Unrecognized identity-free request remains outside the policy

- **WHEN** an identity-free request is untabled, non-native, remote, forwarded,
  capability-disabled, or top-level `/v1`
- **THEN** the native policy is not read or applied
- **AND** existing authentication and routing decide its result

#### Scenario: OAuth identity pair is incomplete

- **WHEN** a request supplies only one of the non-API-key Bearer and account
  header
- **THEN** the OAuth attempt is rejected before pool selection
- **AND** it is not downgraded to an identity-free request

#### Scenario: Forwarded or remote OAuth request

- **WHEN** the peer/Host is non-loopback or any enumerated forwarding header is
  present
- **THEN** OAuth classification fails before authoritative policy or upstream
  work
- **AND** the credential is not silently sent into pool processing

### Requirement: Canonical finite native route table

The OAuth classifier MUST run after the existing path-alias middleware and MUST
use both the ASGI decoded path and `raw_path`. Raw path MUST be ASCII, at most
2048 bytes, and represent the decoded path exactly. Raw query MUST be at most
8192 bytes and, when accepted, SHALL be appended to the fixed target without
semantic rewriting.

The only alias is the existing single collapse of
`/backend-api/codex/v1/<non-empty-rest>` to
`/backend-api/codex/<non-empty-rest>`. The classifier MUST reject a missing
rest, repeated alias prefix, percent-encoded path byte, invalid percent escape,
backslash, NUL, dot segment, duplicate slash, or raw/decoded disagreement.
Exactly one terminal slash MAY be removed only from `/api/codex/usage/` and
`/api/codex/rate-limit-reset-credits/consume/`, matching their existing
registered aliases. Other trailing slashes MUST NOT become OAuth aliases.

After canonicalization, only this method/path/action table is eligible. A
`ChatGPT /...` action means that exact path below the fixed
`https://chatgpt.com/backend-api` origin. `Images adapter -> /codex/responses`
means the existing Codex-base Images validation/translation and response
adapter run with request-local OAuth, then only their normalized Responses
request uses the fixed target; the nonexistent raw `/codex/images/*` target
MUST NOT be invented. `Local direct admission` returns `{"admitted": true}` in
`client_oauth`; under `pool` the existing pool preflight runs after client
identity removal.

| Client method | Canonical client path | OAuth action |
|---|---|---|
| `POST`, `WEBSOCKET` | `/backend-api/codex/responses` | ChatGPT `/codex/responses` |
| `POST` | `/backend-api/codex/responses/compact` | ChatGPT `/codex/responses/compact` |
| `GET` | `/backend-api/codex/models` | ChatGPT `/codex/models` |
| `GET`, `POST` | `/backend-api/codex/thread/goal/get` | ChatGPT `/codex/thread/goal/get` |
| `POST` | `/backend-api/codex/thread/goal/set` | ChatGPT `/codex/thread/goal/set` |
| `POST` | `/backend-api/codex/thread/goal/clear` | ChatGPT `/codex/thread/goal/clear` |
| `POST` | `/backend-api/codex/analytics-events/events` | ChatGPT `/codex/analytics-events/events` |
| `POST` | `/backend-api/codex/memories/trace_summarize` | ChatGPT `/codex/memories/trace_summarize` |
| `POST` | `/backend-api/codex/realtime/calls` | ChatGPT `/codex/realtime/calls` |
| `POST` | `/backend-api/codex/safety/arc` | ChatGPT `/codex/safety/arc` |
| `GET` | `/backend-api/codex/agent-identities/jwks` | ChatGPT `/codex/agent-identities/jwks` |
| `GET` | `/backend-api/wham/agent-identities/jwks` | ChatGPT `/wham/agent-identities/jwks` |
| `GET` | `/backend-api/codex/opportunistic/admission` | Local direct admission / existing pool preflight |
| `POST` | `/backend-api/codex/images/generations` | Images adapter -> ChatGPT `/codex/responses` |
| `POST` | `/backend-api/codex/images/edits` | Images adapter -> ChatGPT `/codex/responses` |
| `POST` | `/backend-api/transcribe` | ChatGPT `/transcribe` |
| `POST` | `/backend-api/files` | ChatGPT `/files` |
| `POST` | `/backend-api/files/{file_id}/uploaded` | ChatGPT `/files/{file_id}/uploaded` |
| `GET` | `/api/codex/usage` | ChatGPT `/wham/usage` |
| `POST` | `/api/codex/rate-limit-reset-credits/consume` | ChatGPT `/wham/rate-limit-reset-credits/consume` |

`file_id` MUST be 1..256 ASCII bytes matching `[A-Za-z0-9._:-]+`. Top-level
`/v1`, internal replica routes, model-source routes, generic SDK routes, and all
unknown method/path pairs MUST retain existing behavior and MUST NOT enter the
OAuth relay.

The relay MUST reuse existing payload budgets: Responses HTTP uses
`max_decompressed_responses_body_bytes` (default 128 MiB), other HTTP uses
`max_decompressed_body_bytes` (default 32 MiB), and downstream Responses
WebSocket ingress uses `--ws-max-size` / `UVICORN_WS_MAX_SIZE` (default
128 MiB). Existing request-model and upstream transport limits remain in force.

#### Scenario: Canonical duplicated-prefix alias is accepted

- **WHEN** an otherwise qualifying request reaches
  `/backend-api/codex/v1/models` after the existing single alias collapse
- **THEN** the classifier matches `GET /backend-api/codex/models`
- **AND** it uses the fixed ChatGPT `/codex/models` action

#### Scenario: Generic top-level v1 request is made

- **WHEN** any request targets `/v1/<rest>` under either policy
- **THEN** it follows existing generic authentication and routing
- **AND** native OAuth classification is not invoked

#### Scenario: Client-OAuth image alias keeps its compatibility adapter

- **WHEN** qualifying OAuth calls a tabled Codex-base Images alias under
  `client_oauth`
- **THEN** the existing Images validation/translation and response adapter run
- **AND** only the normalized Responses request targets fixed ChatGPT
  `/codex/responses`

#### Scenario: Opportunistic admission follows the selected OAuth mode

- **WHEN** qualifying OAuth calls the tabled admission path
- **THEN** `client_oauth` returns local admitted state without upstream I/O
- **AND** `pool` removes client identity before the existing pool preflight

#### Scenario: Encoded or invented path variant is made

- **WHEN** raw path contains an encoded byte, dot segment, duplicate slash, or
  non-tabled trailing slash
- **THEN** it is not accepted as an OAuth route alias
- **AND** no fixed ChatGPT target is constructed from it

### Requirement: Fixed-origin relay and enumerated request headers

Client-OAuth HTTP egress for a tabled ChatGPT action MUST use only
`https://chatgpt.com/backend-api` plus that action's exact fixed path. WebSocket
egress MUST use only
`wss://chatgpt.com/backend-api/codex/responses`. The relay MUST NOT use a
configurable upstream base, model-source URL, request-supplied authority, or
pool proxy target. Redirect following MUST be disabled. Any upstream 3xx MUST
become sanitized HTTP `502` with code
`native_oauth_upstream_redirect_rejected`, without returning `Location`.

The relay SHALL rebuild `Authorization: Bearer <validated request-local
token>`, `chatgpt-account-id`, Host/authority, and Content-Length or transport
framing. It SHALL rebuild `Accept-Encoding: identity`, disable automatic
decompression, use a cookie-less transport, suppress automatic User-Agent and
Cookie generation, and keep normal TLS certificate/hostname verification
enabled. If User-Agent is absent and originator alone admitted the request, the
relay MUST omit rather than invent a User-Agent. It MAY forward only these
case-insensitive inbound header names, each bounded to 16 KiB:

- `accept`, `content-type`, `user-agent`, `originator`, `version`,
  `openai-beta`;
- `request-id`, `x-request-id`;
- `session-id`, `session_id`, `thread-id`, `x-codex-session-id`,
  `x-codex-conversation-id`, `x-codex-turn-state`;
- `x-codex-turn-metadata`, `x-codex-beta-features`,
  `x-codex-installation-id`, `x-codex-parent-thread-id`,
  `x-codex-window-id`, `x-codex-version`;
- `x-openai-subagent`, `x-openai-client-version`, `x-openai-client-os`,
  `x-openai-client-arch`, `x-openai-client-id`, and
  `x-openai-client-user-agent`.

No prefix wildcard is implied. The relay MUST drop inbound Cookie, Origin,
Accept-Encoding, Content-Encoding after decompression, every forwarding or
`cf-*` header, proxy authentication, hop-by-hop headers, headers nominated by
Connection, arbitrary `x-*` headers, and inbound Host, Content-Length,
Authorization, and account headers. An outbound WebSocket Origin MUST be
omitted. Any forwarded value containing NUL, CR, or LF MUST be rejected. Native
route parsing/compatibility normalization MAY still transform the payload
contract; the relay MUST NOT claim byte-for-byte passthrough.

For every direct OAuth WebSocket handshake, after inbound filtering and before
connect, the relay MUST reuse the existing Responses WebSocket beta
normalization: it SHALL remove the incompatible `responses=experimental` token
and ensure exactly one case-insensitive
`responses_websockets=2026-02-06` token in `openai-beta`. This synthesis is
mandatory even when the client omitted `openai-beta`; merely allowing an
inbound value is insufficient.

#### Scenario: Native request contains unsafe and unknown headers

- **WHEN** a qualifying request includes Cookie, a connection-nominated
  header, and an unlisted `x-*` header
- **THEN** none of them is present on the fixed-origin request
- **AND** only rebuilt identity/framing plus enumerated native headers are sent

#### Scenario: Upstream returns a redirect

- **WHEN** the fixed ChatGPT target returns any 3xx response
- **THEN** the relay returns
  `502 native_oauth_upstream_redirect_rejected`
- **AND** it neither follows nor exposes the redirect target

#### Scenario: Direct WebSocket client omits the beta header

- **WHEN** a qualifying direct OAuth WebSocket handshake has no `openai-beta`
- **THEN** upstream receives `openai-beta: responses_websockets=2026-02-06`
- **AND** no incompatible `responses=experimental` token is forwarded

### Requirement: Enumerated streamed response projection

HTTP response status and body SHALL be returned from the fixed target with the
body streamed using the existing bounded streaming behavior. Each returned
header value MUST be at most 16 KiB, and only these case-insensitive response
header names MAY be forwarded:

- `cache-control`, `content-disposition`, `content-encoding`, `content-type`,
  `etag`, `last-modified`, `openai-processing-ms`, `openai-version`, `request-id`,
  `retry-after`, `x-request-id`, `x-should-retry`, `x-codex-turn-state`;
- `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`,
  `x-ratelimit-reset-requests`, `x-ratelimit-limit-tokens`,
  `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens`;
- `x-codex-primary-used-percent`, `x-codex-primary-window-minutes`,
  `x-codex-primary-reset-at`, `x-codex-secondary-used-percent`,
  `x-codex-secondary-window-minutes`, `x-codex-secondary-reset-at`,
  `x-codex-monthly-used-percent`, `x-codex-monthly-window-minutes`,
  `x-codex-monthly-reset-at`, `x-codex-credits-balance`,
  `x-codex-credits-has-credits`, and `x-codex-credits-unlimited`.

Set-Cookie, Set-Cookie2, Location, hop-by-hop, connection-nominated, and all
unlisted headers MUST be dropped. A forwarded response value containing NUL,
CR, or LF MUST also be dropped. A WebSocket accept MAY expose only bounded
`request-id`, `x-request-id`, and `x-codex-turn-state` values from the active
upstream contract.

#### Scenario: Fixed target streams a native response

- **WHEN** the OAuth relay receives an upstream native stream with allowed and
  disallowed response headers
- **THEN** body chunks retain existing native streaming behavior
- **AND** only bounded enumerated response headers reach the client

#### Scenario: Fixed target attempts to set a cookie

- **WHEN** an upstream response includes Set-Cookie or Set-Cookie2
- **THEN** the header is removed
- **AND** no cookie state is retained by the relay transport

### Requirement: Separate request-local client-OAuth branch

Client-OAuth mode SHALL branch before model-source selection, API-key
model/tier enforcement, pooled admission, affinity/account selection,
reservation, owner forwarding, conversation archive, and pooled request-log
creation. It SHALL preserve client method, bounded raw query/body, native
compatibility fields, and streaming. On native Responses and compact paths it
SHALL preserve model, reasoning, tools, and requested service tier without a
policy-specific override. The Images aliases SHALL retain their existing
documented image-model validation and host-Responses translation; neither the
policy nor page may add a model choice. The relay MUST NOT retry a direct
failure through the pool or another OAuth identity.

OAuth credentials and account identifiers MUST remain in a secret-redacted
immutable request/downstream-socket identity and rebuilt outbound headers only.
They MUST NOT appear in database rows, cache entries, object representations,
archives, request logs, metrics labels, exceptions, application logs, or
browser responses.

#### Scenario: Native Responses request uses signed-in OAuth

- **WHEN** `client_oauth` accepts a qualifying native Responses request
- **THEN** the fixed target receives that request's OAuth/account identity and
  client-selected model/reasoning/requested-tier fields
- **AND** model-source, pool selection, owner forwarding, archive, and pooled
  request-log paths are not invoked

#### Scenario: Direct upstream fails

- **WHEN** the client-OAuth upstream returns an error or disconnects
- **THEN** the relay surfaces a sanitized result to that client
- **AND** it does not send the operation under a pool or different OAuth
  identity

### Requirement: Conditional precedence preserves base compatibility contracts

These native-routing requirements SHALL take narrow conditional precedence over
unqualified pooled wording for a tabled request that qualifies for this policy.
This applies only where `responses-api-compat`, `files-upload-protocol`,
`images-api-compat`, `audio-transcriptions-compat`, or
`model-catalog-compat` would otherwise conflict. The following boundaries are
normative:

- Responses validation, contract shaping, streaming, bounded retry-safe replay,
  public error masking, and the base `400 unsupported_input_image_format`
  rejection remain required. `client_oauth` replaces pool account selection,
  owner forwarding, archive ownership, and any cross-policy-source replay with
  this capability's request-local and source/owner-bound rules.
- File payload limits, finalize polling, errors, and synthetic-model accounting
  remain required. `client_oauth` replaces selected-pool-account refresh/retry
  with the request-local identity and the source-provenance rules below.
- Images validation, host-Responses translation, response shape, public model
  accounting, and bounded route telemetry remain required. `client_oauth`
  replaces pooled account/sticky/retry selection with the fixed direct relay.
- Transcription multipart validation, effective-model policy, request budgets,
  and public error envelopes remain required. `client_oauth` replaces selected
  account refresh/retry with the request-local identity and MUST NOT fall back
  to a pool or another OAuth identity.
- `GET /backend-api/codex/models` under `client_oauth` SHALL return the fixed
  upstream catalog for that request identity, subject to bounded native
  compatibility projection; it MUST NOT substitute the local pooled
  bootstrap/refreshed catalog. The `pool` mode and every top-level `/v1/models`
  request retain the existing model-catalog requirements.

Base route-completion telemetry and request-log records that do not require a
selected pool account MUST remain. Any direct-mode record MUST omit account id,
API-key id, bearer-derived values, and account-derived fingerprints. The phrase
"before pooled request-log creation" means that no pool-account association is
created; it MUST NOT suppress safe account-free compatibility telemetry. Every
base requirement outside the exact qualifying native-policy condition SHALL
retain its original authority.

#### Scenario: Direct image alias preserves adapter telemetry without pool routing

- **WHEN** a qualifying Codex-base Images alias runs under `client_oauth`
- **THEN** its existing validation, translation, response adapter, public-model
  accounting, and bounded route-completion telemetry still run
- **AND** no selected pool account, pool retry, or account-associated log is
  created

#### Scenario: Direct transcription bypasses only selected-account behavior

- **WHEN** qualifying native transcription runs under `client_oauth`
- **THEN** existing multipart validation, budget, and public error shaping remain
- **AND** selected-account refresh/retry and pool fallback do not run

#### Scenario: Pool and generic catalog semantics remain unchanged

- **WHEN** `/backend-api/codex/models` uses `pool` or any client calls
  top-level `/v1/models`
- **THEN** the existing bootstrap/refreshed pooled catalog contract remains
  authoritative

### Requirement: Account-scoped continuity is policy-source/owner-bound and fail-closed

Account-scoped continuity state covered by this capability MUST remain
compatible with the turn's policy source kind and, where pool state records an
exact owner, that pool account. This includes `previous_response_id`, supported
`input_file.file_id` references, and file finalize operations. For qualifying
native policy traffic, the server MUST NOT forward such state under an
incompatible policy source kind or to an incompatible exact owner.

Unsupported image-upload references are explicitly outside this provenance
path. When an `input_image` contains `file_id` or an `image_url` beginning
`sediment://`, the base Responses validator MUST return HTTP `400` with
`error.code = "unsupported_input_image_format"` before provenance lookup,
authoritative policy read, policy-source routing, account selection, or upstream
I/O. The native-routing layer MUST NOT reclassify that request as
`native_client_routing_file_source_unavailable`.

For completed direct Responses turns, the service MUST retain only bounded,
expiring source provenance that maps the response id to the literal
`client_oauth` source kind. It MUST NOT retain the bearer, account id, or any
token/account-derived fingerprint. Pool response ownership SHALL continue to
use the existing exact-account continuity state. Before sending a request with
`previous_response_id`, the bound policy source kind and any required exact pool
owner MUST be resolved from live provenance. If provenance is missing, expired,
belongs to another policy source kind, or requires an unavailable pool owner,
the service MAY remove the anchor before first send only when the existing
Responses contract has already prepared a retry-safe, self-contained
full-context body without `previous_response_id`. Under `pool`, such a proven
safe replay MAY use the existing pre-visible eligible-account failover rules;
an owner-bound continuation that is not proven safe MUST fail closed. Otherwise
the service MUST send zero upstream requests and return retryable HTTP `409`
with code `native_client_routing_continuity_source_changed` (or the same stable
code in an accepted WebSocket error event). Incremental tool output and supported
`input_file.file_id` content whose owner/source constraints forbid a fresh turn
MUST NOT be fabricated into one.

After a successful `POST /backend-api/files` returns a valid `file_id`, the
service MUST register bounded, expiring provenance using the existing file-pin
lifetime and locality. Pool mode SHALL keep the existing exact selected-account
pin. Direct mode MUST store only the `file_id`, literal `client_oauth` source
kind, expiry, and non-secret routing metadata; it MUST NOT store a bearer,
account id, or value derived from either. Before finalize or a Responses/compact
request references that file through supported `input_file.file_id`, live
provenance MUST match the bound policy source kind and, for pool provenance, the
exact owner account. Missing, expired, cross-policy-source, or cross-owner
provenance MUST return HTTP `409`
`native_client_routing_file_source_unavailable` before account selection or
upstream I/O. A live pool file pin remains owner-bound and MUST NOT fail over to
a replacement pool account. The error MUST NOT disclose the prior source or
account.

Because direct provenance intentionally contains no account identifier, the
service MUST NOT claim it proves that two `client_oauth` requests use the same
signed-in account. The current request identity is forwarded and upstream
remains authoritative; an external same-mode account change may fail and
require re-upload. These conditional rules MUST NOT alter API-key or generic
top-level `/v1` file behavior.

#### Scenario: Retry-safe response anchor is rebased before a policy-source change

- **GIVEN** a retained turn has a prior-source `previous_response_id` and an
  existing retry-safe self-contained body without that anchor
- **WHEN** the authoritative policy chooses a different policy source kind
- **THEN** the no-anchor body is sent as the initial request to the new source
- **AND** the stale anchored body is sent to no upstream

#### Scenario: Incremental continuation cannot cross a policy-source change

- **GIVEN** a retained turn depends on prior-source incremental tool output and
  has no permitted self-contained no-anchor body
- **WHEN** the authoritative policy chooses another policy source kind
- **THEN** the turn receives
  `native_client_routing_continuity_source_changed`
- **AND** neither policy source kind receives the turn

#### Scenario: Direct file cannot move into the pool

- **GIVEN** a file was registered with live `client_oauth` provenance
- **WHEN** finalize or a turn using its supported `input_file.file_id` observes
  `pool`
- **THEN** it receives `409 native_client_routing_file_source_unavailable`
- **AND** the file id is not forwarded to a selected pool account

#### Scenario: Pool file cannot move to direct OAuth

- **GIVEN** an existing live file pin belongs to a selected pool account
- **WHEN** finalize or a turn using its supported `input_file.file_id` observes
  `client_oauth`
- **THEN** it receives the same source-unavailable error before direct egress
- **AND** the pool account identifier is not exposed

#### Scenario: Direct provenance expires

- **WHEN** direct file provenance is missing or expired under `client_oauth`
- **THEN** the file operation fails closed before upstream I/O
- **AND** the error instructs the client to re-upload without claiming which
  account owned the old file

#### Scenario: Unsupported image-upload reference is rejected before provenance

- **GIVEN** a tabled native Responses or compact HTTP candidate passed bounded
  classification/security admission
- **AND** the authoritative policy database is unavailable
- **WHEN** the request contains an `input_image.file_id` or
  `input_image.image_url` beginning `sediment://`
- **THEN** it receives HTTP `400` with
  `error.code = "unsupported_input_image_format"`
- **AND** no authoritative native-policy read, file provenance, policy-source
  route, pool account, or direct upstream is consulted

### Requirement: OAuth pool mode and account-specific utility semantics

In `pool`, a qualifying OAuth request SHALL have Authorization and
`chatgpt-account-id` removed before entering the existing trusted-loopback pool
path. It SHALL then use existing pool selection, compatibility, affinity,
logging, and accounting.

For qualifying OAuth only, `GET /api/codex/usage` SHALL relay direct signed-in
account usage in `client_oauth`; in `pool` it SHALL use the existing aggregate
pool-usage path after identity removal. Qualifying OAuth
`POST /api/codex/rate-limit-reset-credits/consume` SHALL relay with the
request-local identity in `client_oauth`; in `pool` it MUST return HTTP `409`
with code `native_pool_reset_credit_unavailable` before account selection or
upstream I/O. None of these mode rules SHALL apply to `sk-clb-*`.

#### Scenario: Pool-mode OAuth Responses request

- **WHEN** a qualifying local OAuth request arrives under `pool`
- **THEN** its client identity is removed before existing pool processing
- **AND** exactly the normally selected pool account owns the request

#### Scenario: Pool-mode OAuth usage is aggregate

- **WHEN** qualifying OAuth fetches `/api/codex/usage` under `pool`
- **THEN** it enters the existing aggregate pool-usage path
- **AND** its client OAuth bearer/account header is neither validated against
  nor forwarded to an upstream pool account

#### Scenario: Pool-mode OAuth reset-credit consume is unavailable

- **WHEN** qualifying OAuth posts reset-credit consume under `pool`
- **THEN** it receives sanitized `409 native_pool_reset_credit_unavailable`
- **AND** no client or pool credential is sent upstream

### Requirement: Internal WebSocket upstream rotation at turn boundaries

One server coordinator SHALL preserve the accepted downstream native
WebSocket while binding each active turn to exactly one policy source kind:
`pool` or `client_oauth`. Exact credential identity is a separate level: it is
the request/downstream-socket OAuth identity under `client_oauth` and the
selected pool account under `pool`. A `response.create` that passed the existing
configurable downstream ingress budget MUST be retained while the coordinator
first applies the existing route/protocol-specific base validation. If that
validation succeeds, the coordinator MUST read the authoritative policy and
then validate policy-source/owner-bound continuity for the new turn. A
base-invalid create MUST receive its existing stable validation error without a
native-policy read or upstream send. If the mode then selects a policy source
kind different from the bound upstream, the coordinator MUST finish any active
turn, close only the old upstream, establish an upstream for the newly selected
policy source kind, apply the bounded setup state required by the existing
protocol, and send the prepared initial body under that kind. A revision-only
change whose mode is unchanged SHALL update the bound snapshot without replacing
an otherwise healthy upstream solely because of policy.

Policy change or failure MUST NOT move a turn to another policy source kind.
Under `client_oauth`, the exact OAuth identity MUST remain fixed for the turn;
the relay MUST NOT select another OAuth identity or fall back to `pool`. Under
`pool`, the existing Responses account-selection and safe-replay contracts MAY
replace one selected pool account with another eligible account before
`response.created` or visible output when their existing retry-safety rules
allow it. Existing live-file-pin and previous-response-owner fail-closed
exceptions remain authoritative. A replacement pool account does not change the
turn's `pool` policy source kind.

Every permitted replay or pool-account failover MUST occur before
`response.created` or visible output and MUST NOT re-read policy. After either
event, the turn MUST NOT be replayed or failed over. No path may duplicate a
completed turn. Normal policy changes MUST NOT close the downstream socket or
restart Codex, OpenClaw, or `codex-lb`. If policy read, continuity preparation,
or all source setup allowed within the bound policy source kind fails before
initial send, the retained create MUST be sent to zero upstreams and the
downstream MUST receive a sanitized stable error. If API-key auth became
enabled, that error MUST be
`native_client_routing_api_key_auth_incompatible`. Existing one-active-turn
behavior SHALL govern overlapping create frames; this feature MUST NOT add an
unbounded queue.

#### Scenario: Base-invalid create is rejected before the per-turn policy read

- **GIVEN** a retained `response.create` passed the downstream ingress budget
- **WHEN** existing route/protocol-specific base validation rejects it
- **THEN** the downstream receives the stable base validation error
- **AND** the coordinator performs no authoritative native-policy read, source
  setup, or upstream send for that create

#### Scenario: Policy changes while downstream socket is idle

- **WHEN** the next retained `response.create` observes a newer policy source
  kind
- **THEN** the server replaces only the upstream, validates/rebases any
  account-scoped anchor, and sends the prepared initial body under the fresh
  authoritative snapshot
- **AND** the downstream client connection remains accepted

#### Scenario: Policy changes during a turn

- **WHEN** an upstream response is still in progress as a write commits
- **THEN** that response remains under the policy source kind selected at its
  start
- **AND** the following turn performs a new authoritative read after the
  terminal event

#### Scenario: Revision changes without a policy-source change

- **WHEN** a new turn reads a newer revision with the same valid mode
- **THEN** it binds the newer snapshot and keeps the healthy upstream
- **AND** it keeps the existing policy source kind while retaining compatible
  mode-specific safe-replay behavior

#### Scenario: Policy-source setup fails before create send

- **WHEN** no upstream can be established after every setup attempt permitted
  within the bound policy source kind
- **THEN** the retained create is sent to no upstream and a sanitized error is
  returned downstream
- **AND** the server does not switch policy source kind or select another OAuth
  identity

#### Scenario: Same-policy-kind stale-anchor replay remains available

- **WHEN** the chosen upstream rejects a retry-safe anchor before
  `response.created`
- **THEN** the existing Responses contract may reconnect and replay the
  prepared no-anchor body under the same policy source kind
- **AND** policy is not re-read
- **AND** `client_oauth` keeps the exact OAuth identity while `pool` may use a
  replacement eligible account only when existing pre-visible rules allow it

#### Scenario: Retry-safe pool turn fails over before visibility

- **GIVEN** a turn is bound to `pool`, has no live file pin or unsafe
  previous-response owner dependency, and is retry-safe under the existing
  Responses contract
- **WHEN** selected pool account A fails before `response.created` and visible
  output and existing failover rules allow account exclusion
- **THEN** the coordinator may replay the turn through eligible pool account B
- **AND** the turn remains bound to `pool` and never uses `client_oauth`

#### Scenario: Visible turn is never replayed

- **WHEN** `response.created` or any visible output has been emitted for a turn
- **THEN** neither policy change nor upstream failure replays or fails over that
  turn
- **AND** a completed turn cannot be duplicated

### Requirement: OpenClaw requires a compatible Codex-native provider

OpenClaw SHALL be supported by this policy only when its configured provider
uses the tabled Codex-native endpoint/protocol and supplies an allowlisted Codex
protocol User-Agent prefix or originator plus the qualifying OAuth identity. A
case-insensitive User-Agent suffix token matching
`(?:^|[\s(;])openclaw(?:/|[;\s)]|$)` MAY label attribution as OpenClaw only
after native admission succeeds. The suffix MUST NOT satisfy fingerprint or
authentication checks, and a generic OpenClaw `/v1` provider MUST remain
outside this feature.

#### Scenario: Compatible OpenClaw provider uses native OAuth

- **WHEN** an OpenClaw provider emits an allowlisted Codex fingerprint and all
  other native admission fields on a tabled route
- **THEN** it may follow the stored OAuth policy
- **AND** its suffix may label attribution without granting admission

#### Scenario: OpenClaw suffix is the only native-looking field

- **WHEN** a request has an OpenClaw suffix but no allowlisted Codex prefix or
  originator
- **THEN** it is not classified as native OAuth
- **AND** the suffix cannot expose the policy relay to generic traffic
