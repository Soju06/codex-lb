# Tasks: fixed-endpoint native client routing

A checkbox is completed only after the PR delivering it has merged upstream.
Prototype results may be cited as evidence but do not complete an upstream
task. This change remains active until all required behavior is merged and the
final verification passes.

## 1. Specification

- [ ] 1.1 Define the fixed-endpoint `pool | client_oauth` policy, exact native
  route table, trusted-loopback boundary, and no-secret guarantees
- [ ] 1.2 Define API-key isolation, API-key-auth incompatibility behavior,
  authoritative database consistency, recognized-native missing-identity
  handling, and stable fail-closed errors
- [ ] 1.3 Define policy-source-kind versus exact-identity replay rules, supported
  `input_file.file_id`/finalize provenance, preserved unsupported-`input_image`
  rejection, conditional precedence over affected compatibility capabilities,
  internal turn-boundary upstream rotation, the standalone page, truthful
  evidence language, OpenClaw constraints, and the proposed attribution decision

## 2. Persisted policy and dashboard API

- [ ] 2.1 Add a non-null policy field defaulting to `pool` and use the committed
  singleton settings version as its revision
- [ ] 2.2 Add authenticated capability and policy reads plus a same-origin write
  whose success is returned only after the authoritative transaction commits
- [ ] 2.3 Return `app_restart_required=false` and API-key-auth compatibility in
  the strict policy projection; reject `client_oauth` activation while
  API-key auth is enabled with the specified stable `409`
- [ ] 2.4 Test defaults, migration preservation, no-op set, optimistic conflict,
  read-only/auth/CSRF rejection, capability-off behavior, commit ordering, and
  invalid/unavailable database failure

## 3. Native admission and policy isolation

- [ ] 3.1 Implement the exact method/path canonicalizer, raw-path checks, byte
  bounds, loopback peer/Host checks, and forwarded-header rejection
- [ ] 3.2 Implement the shared Codex fingerprint registry and bounded OAuth
  identity with secret-free representation; treat an OpenClaw suffix as
  attribution only
- [ ] 3.3 Route every `sk-clb-*` request directly to the existing validation,
  restriction, pool, logging, and accounting path without a policy read
- [ ] 3.4 Enforce classifier/security admission -> route-specific/base
  validation -> authoritative policy read -> routing; after validation succeeds,
  read mode, revision, and `api_key_auth_enabled` for each qualifying OAuth HTTP
  request, recognized identity-free safety check, and new WebSocket turn
- [ ] 3.5 Test remote, forwarded, duplicate, malformed, oversized, encoded-path,
  unknown-route, generic `/v1`, unrecognized identity-free invariance,
  recognized identity-free `client_oauth` rejection, pool-mode identity-free
  compatibility, base-invalid no-policy-read behavior, base-error precedence
  over database failure, API-key-auth incompatibility, and database-read
  failures after validation as no-fallback outcomes

## 4. Fixed-origin HTTP relay

- [ ] 4.1 Add the request-local OAuth branch for every tabled HTTP route using
  only the fixed ChatGPT origin, disabled redirects, exact request/response
  header allowlists, and route body limits
- [ ] 4.2 Preserve method, raw query, body, native compatibility fields,
  streaming, model, reasoning, tools, and requested service tier without
  entering model-source or pooled-account selection
- [ ] 4.3 Preserve the explicitly mapped base contracts for Responses, Files,
  Images, Transcriptions, and Models while applying the native-routing spec as
  the narrow conditional authority over conflicting pooled selection behavior;
  execute applicable base validation before authoritative native-policy access
- [ ] 4.4 Reuse the existing Responses WebSocket beta normalization so every
  direct handshake removes `responses=experimental` and ensures exactly one
  `responses_websockets=2026-02-06` token
- [ ] 4.5 In OAuth `pool`, remove the client identity before existing processing,
  preserve aggregate usage, and return the specified reset-credit `409`
- [ ] 4.6 Preserve required account-free route telemetry and compatibility
  request records while proving credentials, account ids, and values derived
  from them cannot enter persistent state, archives, request logs, metrics
  labels, exceptions, browser payloads, or logs
- [ ] 4.7 Add bounded policy-source-kind provenance for direct response ids and
  supported file ids, preserve existing exact pool-owner pins, fail closed on
  missing, expired, or cross-source/cross-owner state, and preserve the base
  `400 unsupported_input_image_format` rejection before policy read or
  provenance routing
- [ ] 4.8 Prove redirects and direct failures never cross into another identity,
  and that `sk-clb-*` usage/reset-credit behavior remains unchanged

## 5. WebSocket turn-boundary rotation

- [ ] 5.1 Add a downstream coordinator that retains one bounded
  `response.create`, performs existing base turn validation, then performs the
  per-turn database read and binds a fresh policy source kind when required;
  base-invalid creates must not read policy
- [ ] 5.2 Keep an in-flight turn on its original policy source kind, rotate only
  the upstream after the terminal event, preserve the downstream socket, keep
  the exact OAuth identity fixed in `client_oauth`, and retain base pool-account
  selection/failover behavior within `pool`
- [ ] 5.3 Rebase a prior-source `previous_response_id` before first send only
  when the existing Responses contract has a prepared retry-safe full-context
  body; otherwise return the stable continuity error with zero upstream sends
- [ ] 5.4 Preserve existing same-policy-kind stale-anchor replay before
  `response.created`/visible output, including allowed pre-visible pool account
  failover and owner/file-bound fail-closed exceptions; prohibit
  cross-policy-kind or cross-OAuth-identity replay and prove a visible or
  completed turn cannot be duplicated
- [ ] 5.5 Return stable sanitized policy/read/continuity/file/setup errors before
  forwarding when their preconditions fail
- [ ] 5.6 Test OAuth-to-pool, pool-to-OAuth, same-mode revision, write/read
  ordering, in-flight update, setup failure, database failure, overlapping
  create, base-invalid create with zero policy reads, base-error precedence over
  database failure, retry-safe rebase, incremental and supported-input-file
  fail-closed behavior, pool account A-to-B pre-visible failover, owner-bound
  fail-closed behavior, same-policy-kind replay, no replay/failover after
  visibility, frame limit, beta-header synthesis, and downstream disconnect
  cleanup

## 6. Dedicated dashboard experience

- [ ] 6.1 Gate navigation, direct route access, and lazy feature activation on
  the disabled-by-default authenticated capability
- [ ] 6.2 Drive the primary CTA only from committed policy and compatibility;
  never mutate client config, model, reasoning, Fast request, or a process
- [ ] 6.3 Explain the fixed endpoint, OAuth substitution, API-key isolation,
  recognized-native missing-identity failure, policy-kind versus exact-identity
  behavior, source/owner-bound response and supported-input-file continuity,
  unsupported-image `400`, re-upload limitation, OpenClaw native-provider
  requirement, and actual-tier/quota evidence boundary
- [ ] 6.4 Provide static one-time attachment guidance only, with no live local
  status, recovery API, process control, or host automation in this scope
- [ ] 6.5 Preserve read-only inspection, responsive desktop/mobile behavior,
  lazy chunk isolation, accessible pending/error states, and record the
  maintainer attribution decision before applying any signature

## 7. Verification and lifecycle

- [ ] 7.1 Run focused and full backend/frontend tests, lint, typecheck, strict
  OpenSpec change validation, all-spec validation, migration checks, and builds
- [ ] 7.2 Verify capability-off, trusted-loopback admission, API-key-auth
  incompatibility, `/v1` invariance, full route coverage, body/header bounds,
  post-validation missing-identity fail-closed behavior, no policy read for base
  validation failures, conditional base-capability contracts,
  policy-kind/exact-identity replay, supported file provenance, unsupported
  image-file rejection, WebSocket beta normalization, secret redaction,
  aggregate pool usage, and API-key regressions
- [ ] 7.3 Run browser-assisted QA on the real page at desktop and mobile widths,
  including console health, read-only mode, pending writes, incompatibility,
  and static guidance
- [ ] 7.4 Run bounded same-client-process E2E
  `client_oauth -> pool -> client_oauth` without app/service restart and prove
  routing from direct-mode account-free records plus selected-account pool
  records; exercise safe anchor rebase, allowed pre-visible pool-account
  failover, source-mismatched supported-file rejection, and unsupported image
  reference rejection; report quota or actual-tier attribution only when
  independent live evidence establishes it
- [ ] 7.5 After implementation PRs merge and final verification passes, sync
  delta specs and archive this change; keep it active until then
