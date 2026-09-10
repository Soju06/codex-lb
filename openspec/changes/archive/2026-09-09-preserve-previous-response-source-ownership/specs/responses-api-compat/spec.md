## MODIFIED Requirements

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

The service MUST generate the non-terminal security advisories described below
before HTTP event normalization. HTTP streams with public OpenAI contract
enforcement enabled, including `/v1/responses` and OpenAI-shaped backend requests,
MUST filter `codex_lb.warning` under the public event-family requirement.
Native Codex HTTP streams with that enforcement disabled MUST retain those
advisories. Filtering an advisory MUST NOT suppress or replace the original
terminal security error. Direct WebSocket advisory delivery remains unchanged.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

#### Scenario: WebSocket authorized retry pool is exhausted after an account failure

- **GIVEN** a safely replayable downstream WebSocket request received a security-work authorization error and retried in the authorized account pool
- **WHEN** authorized accounts fail before response creation, including authentication events or connection failures, and selection exhausts that pool
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"` and returns the original security-work authorization error exactly once
- **AND** the client MUST NOT receive the internal `security_work_authorized_accounts_exhausted` selection error
- **AND** a later independent request on the same WebSocket can acquire the response-create gate and complete
- **AND** this handling MUST NOT replace an account-model rejection fallback or relax owner-pinning and output-exposure replay guards

#### Scenario: HTTP bridge authorized retry pool is exhausted before dispatch

- **GIVEN** a safely replayable HTTP bridge request received a security-work authorization error
- **WHEN** the last authorized replacement is excluded after a transient credential-refresh failure or repeated upstream authentication failure before retry dispatch
- **THEN** codex-lb MUST emit a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"` and `action="forward_original_security_work_error"` before returning the original security-work authorization error exactly once
- **AND** the client MUST NOT receive the internal `security_work_authorized_accounts_exhausted` selection error
- **AND** the retry MUST preserve the existing owner-pinning and output-exposure replay guards and complete request cleanup

#### Scenario: Public HTTP filtering preserves the terminal security denial

- **GIVEN** HTTP bridge authorized-pool exhaustion generates a missing-pool advisory and the original security denial
- **WHEN** the request uses `/v1/responses` or an OpenAI-shaped backend stream with public contract enforcement
- **THEN** the public stream MUST omit `codex_lb.warning` and retain the original security denial exactly once
- **AND** a native backend stream with enforcement disabled MUST retain the advisory before the denial

#### Scenario: Pinned requests are not moved to another account

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **THEN** codex-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure.

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** codex-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt

## ADDED Requirements

### Requirement: Previous-response source routing follows proven ownership

When a Responses request carries `previous_response_id`, the proxy MUST resolve
recorded subscription-account ownership independently from model-source catalog
ownership. The proxy MUST NOT infer either outcome from the response identifier's
syntax. A recorded subscription owner MUST keep the request on subscription
routing. A missing subscription owner MUST first be evaluated against the
model-source catalog. If the source catalog confirms source ownership, the
configured model source remains authoritative even when exactly one subscription
account is in scope; the request MUST NOT use subscription candidate fallback.
For HTTP and compact subscription routing, possible-owner counting is
permitted only after the source catalog lookup succeeds without confirming
source ownership, applying API-key account-assignment scoping. Exactly one
possible owner MUST be allowed to proceed through normal subscription
selection; zero or multiple possible owners MUST fail closed with the sanitized
`previous_response_owner_unavailable` error. Account-pinned file requests remain
strict and do not use the sole-candidate fallback. Codex compaction remains
subscription-only; its dedicated compact HTTP selection uses the same
sole-candidate compatibility fallback. The direct Responses WebSocket transport
MUST retain its `model_source_requires_http_transport` fallback for confirmed
source ownership. An unavailable source-catalog lookup is distinct from an owner
miss: the direct WebSocket path MUST preserve its existing subscription fallback
instead of applying the successful-catalog precondition or converting the lookup
failure into `previous_response_owner_unavailable`.
A client-supplied nonblank `x-codex-turn-state` that does not match the
proxy-synthesized marker shapes is hard continuity evidence. When that token
cannot be resolved to an owner in the requesting API-key scope, the proxy MUST
fail closed and MUST NOT apply the sole-candidate fallback, even when exactly
one subscription account is in scope. Proxy-synthesized first-turn
placeholders (`turn_*` / `http_turn_*`) are compatibility markers rather than
hard account ownership. In an owner-miss continuation, when a marker alias is
unavailable, a value matching either shape MUST use the same sole-candidate
compatibility rule without requiring exact server-side issuance provenance; a
registered marker MUST still resolve to its recorded owner.
Whenever previous-response or unregistered synthesized-marker sole-candidate
fallback is applicable, possible owners MUST be counted from the account collection
visible under the existing deletion lifecycle, restricted only by API-key account-assignment
scope. Model and service-tier support, catalog omissions, account health/status,
quota or capacity availability, security authorization, and retry exclusions MUST
NOT remove possible owners from this count. Exactly one possible owner permits
normal selection to attempt that account; zero or multiple possible owners MUST
fail closed. Subsequent selection and socket reuse MUST still enforce all routing
constraints, including security authorization. This owner-miss count MUST NOT
change the separate model/API-key/security-scoped conversation ambiguity contract.
Direct WebSocket requests MUST reconcile registered turn-state ownership with
previous-response and file ownership before either initial-connect or socket-reuse
model-source guards. A resolved turn-state subscription owner MUST veto source
HTTP fallback, including synthetic-shaped aliases and requests without a
previous response. Conflicting independent owners MUST fail closed before
upstream dispatch; an unregistered marker or a socket-local injected anchor
MUST NOT by itself establish registered turn-state ownership.
HTTP turn-state lookup MUST NOT be skipped merely because there is no
`previous_response_id` and enabled-source selection found no match. A failed
disabled-source subscription-owner lookup MUST retain the resolver's sanitized
HTTP 502 status and error payload at both HTTP route boundaries.
A resolved previous-response owner and an account-pinned file owner remain independent
hard constraints, and marker shape
MUST NOT override either owner or relax strict file routing. For the same
fail-closed decision, an empty or whitespace-only `x-codex-turn-state` value
MUST normalize to blank client input while preserving physical-header presence.
Such a value MUST NOT establish ownership, be treated as an omitted header,
or authorize synthesized-marker compatibility or sole-candidate
fallback. Shape-based marker compatibility applies across HTTP bridge forwards,
direct Responses WebSocket reconnects, and compact request echoes when local
marker aliases are unavailable; exact process-local issuance provenance is not
required.
When an authenticated internal owner forward carries a synthesized marker, the
`x-codex-bridge-synthesized-turn-state-signature` MUST bind that marker to the
posted body and context. The existing `x-codex-bridge-signature-v2`
tools-bound signature MUST retain its pre-marker field shape so an older owner
can verify marker-bearing forwards, including forwards carrying file-owner
proof. When no synthesized marker is present, the marker field MUST be omitted
from every signing shape and the marker-proof header MUST NOT be emitted, preserving
pre-marker v2 owner verification.

#### Scenario: Marker provenance stays compatible during owner forwarding

- **GIVEN** an updated origin forwards a request with a synthesized marker and a file-owner proof
- **WHEN** a pre-marker owner verifies the `x-codex-bridge-signature-v2` header
- **THEN** that header matches the pre-marker signing shape and the owner accepts the forward
- **WHEN** an updated owner verifies the same forward
- **THEN** the additive `x-codex-bridge-synthesized-turn-state-signature` proves the marker and the owner accepts it

#### Scenario: Marker-free owner forwarding keeps the pre-marker signing shape

- **GIVEN** an owner forward carries no synthesized marker
- **WHEN** the origin emits the tools-bound v2 signature
- **THEN** the signing shape omits the marker field and the marker-proof header is absent

#### Scenario: Recorded subscription owner overrides an HTTP model source

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is not forwarded to the model source
- **AND** subscription routing preserves the recorded account owner

#### Scenario: Recorded subscription owner overrides a disabled HTTP model source

- **GIVEN** a Responses-compatible source is configured but disabled for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is not rejected as `model_source_disabled`
- **AND** subscription routing preserves the recorded account owner

#### Scenario: Registered marker-only ownership survives an enabled-source miss

- **GIVEN** an HTTP Responses request has no `previous_response_id`
- **AND** its `turn_*` or `http_turn_*` marker is registered to a subscription account in the requesting API-key scope
- **AND** enabled-source lookup finds no match
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the proxy resolves that registered marker before treating the request as ownerless
- **AND** subscription routing preserves the registered owner even if a disabled source also claims the model

#### Scenario: Disabled-source owner lookup failure keeps its sanitized error

- **GIVEN** a disabled source claims the requested model
- **AND** the subscription previous-response owner resolver raises a sanitized HTTP 502 error
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the route returns that status and sanitized error payload
- **AND** it does not replace the failure with a generic HTTP 500 or dispatch to an upstream

#### Scenario: Canonical source response ID remains source-routed over HTTP

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** the source catalog confirms that the requested model is source-owned
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source

#### Scenario: Opaque source response ID remains source-routed over HTTP

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `source-turn-opaque-42`
- **AND** the source catalog confirms that the requested model is source-owned
- **AND** `previous_response_id` is the opaque non-canonical value `source-turn-opaque-42`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source
- **AND** routing is based on recorded ownership rather than identifier syntax

#### Scenario: Confirmed source ownership outranks a sole subscription candidate

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `source-turn-sole-candidate`
- **AND** the source catalog confirms that the requested model is source-owned
- **AND** exactly one possible subscription owner remains after applying
  API-key account-assignment scoping
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source
- **AND** the sole subscription candidate is not selected

#### Scenario: Known subscription-model owner miss fails closed over HTTP

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** zero or multiple possible subscription owners remain after applying
  API-key account-assignment scoping
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the proxy returns HTTP status `502`
- **AND** the sanitized error code is `previous_response_owner_unavailable`
- **AND** the sanitized error message is `Previous response owner account is unavailable; retry later.`
- **AND** no subscription account is selected and no upstream request is dispatched

#### Scenario: Sole possible subscription owner preserves an HTTP continuation

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** exactly one possible subscription owner remains after applying
  API-key account-assignment scoping
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the proxy proceeds through normal subscription account selection
- **AND** the request is forwarded to that sole possible subscription owner only if it passes normal routing checks
- **AND** the `previous_response_id` is preserved in the upstream request

#### Scenario: Unresolved client turn-state blocks the HTTP sole-candidate fallback

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** the client supplies an `x-codex-turn-state` with no owner in the
  requesting API-key scope
- **AND** exactly one possible subscription owner remains
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the proxy returns HTTP status `502`
- **AND** the sanitized error code is `previous_response_owner_unavailable`
- **AND** no subscription account is selected and no upstream request is dispatched

#### Scenario: Blank client turn-state blocks the HTTP sole-candidate fallback

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** the client sends a physically present `x-codex-turn-state` header
  whose value is empty or whitespace-only
- **AND** exactly one possible subscription owner remains
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the proxy returns HTTP status `502`
- **AND** the sanitized error code is `previous_response_owner_unavailable`
- **AND** no subscription account is selected and no upstream request is dispatched

#### Scenario: Synthetic-shaped turn state keeps the compatibility fallback

- **GIVEN** a direct Responses WebSocket continuation has a missing previous-response owner
- **AND** the client supplies an unregistered `turn_*` or `http_turn_*` marker
- **AND** no independently resolved previous-response or file owner constrains the request
- **AND** exactly one possible subscription owner remains after API-key account-assignment scoping
- **WHEN** the client submits the continuation
- **THEN** the proxy proceeds through normal subscription selection for that sole account
- **AND** no exact server-side issuance provenance is required
- **AND** the marker shape alone does not select an account when zero or multiple possible owners remain

#### Scenario: Independent hard ownership remains authoritative over marker shape

- **GIVEN** a request carries an unregistered `turn_*` or `http_turn_*` marker
- **AND** a recorded previous-response owner or live input-file pin resolves to an account
- **WHEN** the client submits the Responses or compact continuation
- **THEN** routing remains constrained to that independently resolved owner
- **AND** marker-shape compatibility does not override the owner or relax file routing

#### Scenario: Supplied synthetic marker keeps HTTP bridge owner-miss compatibility

- **GIVEN** HTTP bridge routing is enabled and the previous-response owner is missing
- **AND** the client physically supplies an unregistered `turn_*` or `http_turn_*` header
- **AND** no file owner or required durable bridge owner constrains the request
- **WHEN** the client calls either HTTP Responses route
- **THEN** the bridge applies the API-key-scoped sole-candidate check without requiring local marker issuance
- **AND** exactly one possible owner permits normal routing checks while zero or multiple candidates fail closed
- **AND** blank and non-synthetic client headers do not authorize this fallback

#### Scenario: Turn-state ownership bypasses owner-miss candidate fallback

- **GIVEN** no subscription account is recorded as owner of `previous_response_id`
- **AND** a turn-state header identifies a subscription account owner
- **WHEN** the client submits an HTTP Responses or compact continuation
- **THEN** the proxy reconciles the turn-state owner through normal owner resolution
- **AND** the proxy does not apply the zero-or-multiple-candidate owner-miss failure

#### Scenario: Unregistered synthetic compact marker without previous response uses sole candidate

- **GIVEN** a compact request has no `previous_response_id`
- **AND** the client supplies an unregistered `turn_*` or `http_turn_*`
  `x-codex-turn-state` in the requesting API-key scope
- **AND** exactly one possible subscription owner remains
- **WHEN** the client calls `/backend-api/codex/responses/compact` or
  `/v1/responses/compact`
- **THEN** the proxy proceeds through normal compact subscription selection
- **AND** the request is forwarded to that sole possible subscription owner only if it passes normal routing checks
- **AND** the unregistered marker does not by itself produce
  `turn_state_owner_unavailable`

#### Scenario: Marker-only compact owner miss rejects an ambiguous pool

- **GIVEN** a compact request has no `previous_response_id` and its synthetic-shaped marker resolves to no owner
- **AND** no independent file owner constrains the request
- **AND** zero or multiple possible subscription owners remain after API-key account-assignment scoping
- **WHEN** the client calls either compact HTTP route
- **THEN** the proxy returns HTTP 502 with `previous_response_owner_unavailable`
- **AND** no account is selected and no upstream request is dispatched
- **AND** any API-key reservation is settled before error-health writes

#### Scenario: Direct WebSocket preserves a recorded subscription owner

- **GIVEN** a source is also configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the request remains on the owner-bound subscription WebSocket path
- **AND** the proxy does not emit `model_source_requires_http_transport`

#### Scenario: Registered turn-state ownership overrides a WebSocket model source

- **GIVEN** a `turn_*` or `http_turn_*` alias is registered to a subscription account in the requesting API-key scope
- **AND** a source also claims the requested model
- **WHEN** either direct Responses WebSocket route handles an initial request or a request on a reused socket
- **THEN** the proxy reconciles the alias with any independent previous-response and file owners before source fallback
- **AND** matching ownership preserves the required subscription account without emitting `model_source_requires_http_transport`
- **AND** conflicting ownership fails closed without upstream dispatch
- **AND** any client-supplied previous-response anchor is preserved

#### Scenario: Direct WebSocket source continuation falls back to HTTP

- **GIVEN** a source is configured for the requested model
- **AND** the source catalog confirms that the requested model is source-owned
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits `model_source_requires_http_transport`
- **AND** the service-level error uses HTTP status `503`
- **AND** the request is not sent to a subscription upstream

#### Scenario: Opaque source response ID falls back to HTTP on direct WebSocket

- **GIVEN** a source is configured for the requested model
- **AND** the source catalog confirms that the requested model is source-owned
- **AND** no subscription account is recorded as owner of `source-turn-opaque-ws`
- **AND** `previous_response_id` is the opaque non-canonical value `source-turn-opaque-ws`
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits `model_source_requires_http_transport`
- **AND** the request is not sent to a subscription upstream

#### Scenario: Known subscription-model owner miss fails closed on direct WebSocket

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** zero or multiple possible subscription owners remain after applying
  API-key account-assignment scoping
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits a terminal error with HTTP status `502`
- **AND** the sanitized error code is `previous_response_owner_unavailable`
- **AND** the sanitized error message is `Previous response owner account is unavailable; retry later.`
- **AND** no subscription account is selected and no upstream request is dispatched

#### Scenario: Model or service-tier support does not resolve ownership ambiguity

- **GIVEN** an owner-miss continuation may use the sole-candidate compatibility fallback
- **AND** the API-key assignment scope contains accounts A and B
- **AND** A omits the requested model or service tier from its catalog while B supports it
- **WHEN** an HTTP stream, HTTP bridge, compact, or direct WebSocket route processes the request
- **THEN** both accounts MUST remain possible owners and the request MUST fail closed with `previous_response_owner_unavailable`
- **AND** no upstream request MUST be dispatched

#### Scenario: Account assignment can establish a sole possible owner

- **GIVEN** an owner-miss continuation may use the sole-candidate compatibility fallback
- **AND** only account B belongs to the requesting API key's assignment scope
- **AND** B passes normal routing checks
- **WHEN** an HTTP stream, HTTP bridge, compact, or direct WebSocket route processes the request
- **THEN** B MUST be the sole possible owner and receive the request with its previous-response identifier preserved

#### Scenario: Security capability does not resolve WebSocket ownership ambiguity

- **GIVEN** a direct WebSocket continuation requires the trusted-cyber capability and its previous-response owner is unknown
- **AND** the API-key scope contains one security-work-authorized and one non-authorized possible owner
- **WHEN** either direct Responses WebSocket route processes the continuation
- **THEN** both accounts remain in the ownership count and the proxy emits `previous_response_owner_unavailable`
- **AND** no upstream continuation is dispatched, including on an already-open authorized socket

#### Scenario: Sole WebSocket owner still requires security authorization

- **GIVEN** a direct WebSocket continuation requires the trusted-cyber capability
- **AND** ownership resolution yields one possible or independently proven account lacking security-work authorization
- **WHEN** either direct Responses WebSocket route selects or reuses an account
- **THEN** the proxy rejects dispatch under the existing capability policy without switching to another owner

#### Scenario: Direct WebSocket candidate lookup failure fails closed

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** the continuation is not already attached to its required open owner socket
- **AND** loading possible subscription owners fails
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits the sanitized `previous_response_owner_unavailable` terminal error
- **AND** the lookup failure is not exposed to the client

#### Scenario: Sole possible subscription owner preserves a WebSocket continuation

- **GIVEN** the requested model is known to subscription routing
- **AND** the source catalog lookup succeeds without confirming source ownership
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** exactly one possible subscription owner remains after applying
  API-key account-assignment scoping
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy proceeds through normal subscription account selection
- **AND** the request is forwarded to that sole possible subscription owner only if it passes normal routing checks
- **AND** the `previous_response_id` is preserved in the upstream request

#### Scenario: Unavailable source-catalog lookup preserves subscription fallback

- **GIVEN** no subscription account is recorded as owner of `previous_response_id`
- **AND** the source-catalog lookup for the requested model is unavailable
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy preserves the existing subscription account-selection path
- **AND** the request is forwarded to the selected subscription upstream
- **AND** the proxy does not emit `previous_response_owner_unavailable`
