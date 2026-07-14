## ADDED Requirements

### Requirement: Dedicated native-routing dashboard destination

When the authenticated server capability reports enabled, the dashboard SHALL
expose Native Routing as a prominent top-level navigation item and dedicated
`/native-routing` page. General Settings SHALL NOT duplicate the policy control,
and this change SHALL NOT add a menu-bar application.

When capability is false, invalid, unauthorized, or unavailable, desktop and
mobile navigation MUST omit Native Routing, direct route access MUST redirect
to `/dashboard`, and the lazy feature provider/page MUST NOT activate. The
frontend MUST NOT infer support from browser platform, hostname, or loopback
addressing.

The page SHALL remain a standalone lazy feature module so later clients and
visual design can evolve without coupling to General Settings. It SHALL load
only authenticated capability/policy APIs in this scope and MUST NOT contact a
browser loopback port, inspect local files, or invoke host-side automation.

#### Scenario: Operator opens native routing

- **WHEN** capability is enabled and the operator selects Native Routing
- **THEN** the dedicated lazy page loads committed policy through authenticated
  same-origin APIs
- **AND** no local-file, process, or host-automation request is made

#### Scenario: Capability is disabled or unavailable

- **WHEN** capability is false, invalid, unauthorized, or unavailable
- **THEN** neither navigation variant exposes Native Routing
- **AND** direct `/native-routing` access redirects to `/dashboard`
- **AND** the feature provider, page chunk, and policy API are not activated by
  the frontend

#### Scenario: Responsive independent page

- **WHEN** the page renders at supported desktop or mobile widths
- **THEN** its primary control, compatibility state, flow explanation, static
  attachment guidance, and decision-approved attribution fit without
  horizontal overflow
- **AND** all page composition remains inside the standalone feature module

### Requirement: One committed credential-source policy action

The visually primary action SHALL change only the persisted server policy. In
`pool`, when OAuth switching is available, it SHALL offer **Use signed-in OAuth
account** and submit exactly one update to `client_oauth`. In `client_oauth` it
SHALL offer **Use codex-lb account pool** and submit exactly one update to
`pool`.

Routine policy mutation MUST NOT edit client configuration, select a model,
change reasoning or requested Fast, run inference, restart a client/service, or
perform attachment/recovery work. The page MUST update its visible state only
from the committed mutation response and MUST treat
`app_restart_required=false` as a strict contract rather than a restart
instruction.

#### Scenario: Pool is active and compatible

- **WHEN** policy reports `pool` and `oauth_switching_available=true`
- **THEN** the primary action offers Use signed-in OAuth account
- **AND** activation sends exactly one policy update to `client_oauth`

#### Scenario: Client OAuth is active

- **WHEN** policy reports `client_oauth` and the user has write permission
- **THEN** the primary action offers Use codex-lb account pool
- **AND** activation sends exactly one policy update to `pool`

#### Scenario: Policy update is pending

- **WHEN** a policy mutation is awaiting its response
- **THEN** competing policy actions are disabled
- **AND** assistive technology receives a localized polite progress update
- **AND** the frontend does not automatically repeat an ambiguous mutation

### Requirement: API-key-auth incompatibility is prominent and fail-closed

The page MUST render `oauth_switching_available` and `incompatibility_code`
from the policy API. When unavailable, it SHALL explain that native OAuth
switching inherits the local no-key trust model and therefore requires
API-key auth to be disabled; it MUST NOT suggest another secret/header or claim
that OAuth can satisfy the API-key guard.

When mode is `pool` and OAuth switching is unavailable, the action to set
`client_oauth` MUST be disabled. If an existing `client_oauth` mode becomes
incompatible, the page SHALL preserve that committed label, show the stable
incompatibility, and MAY allow a writer to set `pool`; it MUST NOT silently
relabel or mutate policy. The page SHALL state that `sk-clb-*` clients retain
their existing behavior under both policy values.

#### Scenario: API-key auth is enabled while pool is stored

- **WHEN** policy reports `oauth_switching_available=false` and `mode=pool`
- **THEN** signed-in OAuth activation is disabled with the stable explanation
- **AND** API-key clients are described as policy-independent

#### Scenario: Existing client-OAuth policy becomes incompatible

- **WHEN** policy reports `mode=client_oauth` and the incompatibility code
- **THEN** the page keeps `client_oauth` as the configured value and shows that
  OAuth turns fail closed
- **AND** it does not infer or display an automatic pool transition

### Requirement: Static one-time attachment guidance only

If provided, static attachment guidance SHALL remain visually secondary and
limited to attaching Codex App/CLI and a compatible OpenClaw Codex-native
provider to `http://127.0.0.1:2455/backend-api/codex`. It SHALL explain that
attachment is a one-time prerequisite distinct from the routine policy action.

This proposal MUST NOT expose live attachment/config/proxy-health status,
backup paths, setup/repair/rollback operations, process restart controls, or
fresh-status actions. The static guidance MUST NOT claim that either client is
currently attached. Any future host integration requires a separate reviewed
contract.

#### Scenario: Operator reads attachment guidance

- **WHEN** the page shows setup information for Codex or OpenClaw
- **THEN** it presents instructions and the fixed endpoint without claiming
  local configuration was inspected
- **AND** no action on the page changes files or process state

#### Scenario: Client is not actually attached

- **WHEN** the operator has not completed the external one-time instructions
- **THEN** the page still labels only the committed server policy
- **AND** it does not fabricate attachment health or offer an in-scope repair

### Requirement: Truthful fixed-route and OpenClaw explanation

The page SHALL explain that both OAuth policy values keep the same native
endpoint and client-selected model, reasoning, tools, and requested service
tier. It SHALL distinguish forwarding a qualifying signed-in client OAuth
identity from removing it and substituting a selected pool identity behind
`codex-lb`. It SHALL explain that a turn's policy source kind never changes
mid-turn: `client_oauth` keeps the exact request OAuth identity and never falls
back to the pool, while `pool` may replace its selected account only through
existing retry-safe pre-visible failover rules. It MUST state that no replay or
failover occurs after `response.created` or visible output.

The explanation MUST state that top-level `/v1` and all `sk-clb-*` requests are
outside the policy. OpenClaw SHALL be described as supported only through a
compatible Codex-native provider that emits an allowlisted Codex protocol
fingerprint and OAuth identity; an OpenClaw User-Agent suffix alone MUST NOT be
presented as authentication.

The page MUST state that `client_oauth` requires the recognized native request
to carry both OAuth identity fields after applicable base request validation.
If an otherwise recognized, base-valid native request omits both, it fails with
`native_client_oauth_identity_required` rather than silently using a pool
account. A base-invalid request keeps its base error without a policy read. This
explanation MUST NOT imply that the page can inspect the live client headers.

The page SHALL show a persistent, localized source-continuity warning adjacent
to the policy action. It MUST explain that response anchors, supported
`input_file.file_id` references, and file finalize operations are
account-scoped; unsafe incremental continuations fail closed on a policy-source
change; and supported input-file work may require re-upload after switching
source, restarting the service, provenance expiry, or changing the externally
signed-in account. It MUST separately explain that `input_image.file_id` and
`sediment://` image references are unsupported and receive the base
`400 unsupported_input_image_format` rejection before authoritative policy
read, provenance, or routing, not a re-uploadable-file `409`. It MUST NOT claim
to inspect, list, migrate, repair, or verify live files or same-account
continuity.

The page MUST NOT claim byte-for-byte passthrough, a Fast grant, actual upstream
tier, completed live inference, or exact quota attribution from policy or
configuration state. It SHALL state that those runtime/account claims require
bounded live evidence.

#### Scenario: Operator reads the flow

- **WHEN** either OAuth policy value is selected
- **THEN** the diagram keeps the same client and fixed native endpoint in both
  branches
- **AND** only the qualifying OAuth identity/account branch changes
- **AND** it distinguishes fixed direct OAuth identity from permitted
  pre-visible pool-account failover within the unchanged `pool` policy kind
- **AND** API-key and generic `/v1` paths are visibly separate

#### Scenario: Recognized native client omits OAuth identity

- **WHEN** the operator reads the `client_oauth` branch for a base-valid request
- **THEN** the page explains that a missing OAuth pair fails closed rather than
  selecting the pool
- **AND** it does not claim to know whether the currently attached client sends
  those headers

#### Scenario: Operator switches with account-scoped work in progress

- **WHEN** either policy action is available
- **THEN** the page warns that stale incremental anchors cannot move sources and
  supported `input_file.file_id` work may require re-upload
- **AND** it distinguishes unsupported image-upload references and their base
  `400 unsupported_input_image_format` rejection, with no policy read, from file
  provenance failures
- **AND** the warning does not claim that any live file or turn was inspected

#### Scenario: Policy reports a requested service-tier-preserving route

- **WHEN** the page explains that the client request is not overridden
- **THEN** it describes requested Fast separately from actual upstream tier
- **AND** it makes no quota-owner claim without independent live evidence

### Requirement: Dashboard permission-aware controls

Policy, compatibility, flow explanation, and static attachment guidance SHALL
remain visible to sessions with read permission. Policy mutation MUST be
disabled or omitted without write permission. Errors MUST be localized,
sanitized, and preserve the last confirmed committed projection; an uncertain
write MUST require an explicit fresh policy read before another mutation.

#### Scenario: Read-only dashboard session

- **WHEN** a session has read but not write permission
- **THEN** committed policy and all explanatory content remain visible
- **AND** no enabled control can change policy, config, or process state

#### Scenario: Mutation result is ambiguous

- **WHEN** transport fails after a policy write was submitted
- **THEN** the page does not assume either policy value
- **AND** it requires an explicit committed-policy refresh before enabling the
  next write

### Requirement: Attribution is an explicit maintainer decision

Before frontend implementation is treated as complete, maintainers MUST record
whether the proposed `DOMANHDUC · dmdfami` signature is accepted. If accepted,
the page SHALL render that exact string unchanged in every locale. If declined,
the functional Native Routing page SHALL remain valid without it. The signature
MUST NOT be introduced as an undisclosed acceptance or merge condition.

#### Scenario: Maintainers accept the proposed attribution

- **WHEN** the recorded decision approves the signature
- **THEN** every locale renders `DOMANHDUC · dmdfami` unchanged
- **AND** localization does not rewrite the names

#### Scenario: Maintainers decline the proposed attribution

- **WHEN** the recorded decision declines the signature
- **THEN** the page omits it without changing routing behavior
- **AND** functional acceptance does not fail on its absence
