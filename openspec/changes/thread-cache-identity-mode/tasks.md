## 1. Specification

- [x] 1.1 Add the `responses-api-compat` requirement for the two modes, the
      three scoped legs, prefix stability, the shape-aware injection point and
      the byte-identical `shared` guarantee.
- [x] 1.2 Condition the two existing "forwarded unchanged" clauses on `shared`
      mode, copying every existing scenario into the MODIFIED blocks.
- [x] 1.3 Add the `api-keys` requirement for the per-key override and its
      precedence.

## 2. Configuration

- [x] 2.1 Add the T3 `Settings` field, its `SETTING_TIERS` entry and the
      `[settings_fields].max` 96 -> 97 ratchet with the why-not-a-default
      justification.
- [x] 2.2 Add the nullable `dashboard_settings` column, seeded NULL, wired
      through the repository, service, schemas and settings API as an
      inheritable setting with provenance.
- [x] 2.3 Add the nullable `api_keys.thread_cache_identity_override` with the
      strict and lenient normalizer pair, the `_Unset` update sentinel and the
      `*_set` patch flag.
- [x] 2.4 Add the alembic migration with `down_revision` set to the real head.

## 3. Implementation

- [x] 3.1 Add the identity module with the deterministic account-only scope
      token and the three pure scoping functions, each an immediate no-op in
      `shared` mode.
- [x] 3.2 Apply the payload legs at the single streaming chokepoint, above the
      HTTP/websocket fork and above the payload size estimate; scope the
      session headers after the header builders return, position-preserving.
- [x] 3.3 Apply the same two legs in the compact transport, strictly before the
      compact wire-budget validator.
- [x] 3.4 Resolve the mode once per request in the service layer, where the API
      key and the settings snapshot are in scope, and thread it to the core
      client; the core client never reads the database.
- [x] 3.5 Record the resolved mode and whether it came from the key override in
      the request-shape trace.

## 4. Regression coverage

- [x] 4.1 Byte-identity under `shared`: serialized HTTP, websocket
      `response.create` and compact payloads plus the outbound header list are
      identical to the pre-change output.
- [x] 4.2 Stability: consecutive turns of one session on one account produce
      the identical token, including when the second turn carries no
      client-supplied session identifier.
- [x] 4.3 Turn-state immunity: a fresh `x-codex-turn-state` between turns does
      not change the token, and the header itself is never rewritten.
- [x] 4.4 Divergence: the same session on two accounts produces different
      tokens, scoped keys and scoped headers.
- [x] 4.5 Model purity: after an `isolated` request the `ResponsesRequest`
      model's `prompt_cache_key` and the inbound headers mapping are unchanged.
- [x] 4.6 Responses-lite branch: a lite payload gets a leading input item and
      keeps `instructions == ""`.
- [x] 4.7 Override resolution: key override beats dashboard beats environment
      beats the `shared` default.
- [x] 4.8 Compact wire-budget validator observes the post-injection size.
- [x] 4.9 An unrecognised configured mode — environment, dashboard column or
      key override — degrades to `shared` instead of escaping into the
      pattern-constrained settings response model.
- [x] 4.10 A maximum-length client `prompt_cache_key` stays within the upstream
      length bound once scoped, while staying unique per account and per client
      key.
- [x] 4.11 A client key that resembles an already-scoped value is still scoped
      per account, and the declared payload overhead bounds the real injection.
- [x] 4.12 The coverage boundary is pinned by a test, so wiring the bridge or
      the direct WebSocket surface is a deliberate spec update rather than a
      silent drift.

## 6. Deferred

- [ ] 6.1 Extend `isolated` to the HTTP session bridge, which is enabled by
      default and serializes its request text before account selection.
- [ ] 6.2 Extend `isolated` to the direct downstream WebSocket surface, whose
      relayed frame text doubles as the dispatch-owner and replay key.

## 5. Validation

- [x] 5.1 Ruff check and format on the touched files.
- [x] 5.2 Affected unit suites.
- [x] 5.3 `openspec validate thread-cache-identity-mode --strict`.
