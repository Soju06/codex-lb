## Context

The direct Responses WebSocket ingress already accepts one authenticated `trusted_cyber` carrier and applies the existing `security_work_authorized` selector constraint before opening an upstream connection. The standard Codex client example defines only the ordinary `codex-lb` provider, so Codex Desktop/CLI orchestration has no explicit opt-in configuration that sends the carrier. Provider and profile selection are machine-local Codex settings; current Codex versions ignore them in project-local `.codex/config.toml` files. Current Codex clients treat `supports_websockets = true` as a preference and can retry the same provider request over HTTP; they expose no stable WebSocket-only or fallback-off provider setting.

## Goals / Non-Goals

**Goals:**

- Publish a machine-local Codex configuration with separate ordinary and Daybreak Blue providers.
- Make `--profile daybreak-blue` select the Daybreak provider and canonical `gpt-5.6-sol` model.
- Send the exact trusted-cyber carrier on every Daybreak provider request, including the first request, while leaving ordinary provider requests unchanged.
- Authenticate and fail closed before routing if the Daybreak carrier reaches any unsupported HTTP route or non-Responses WebSocket, while allowing authenticated model-catalog initialization.
- Prove the checked-in configuration against an installed Codex client, the real direct-WebSocket capability-ingress and first-selection seam, and the HTTP fallback seam without external requests or credentials.

**Non-Goals:**

- Granting Trusted Access, discovering approved identities, or deriving authorization from a model slug, prompt, skill, user agent, or task content.
- Adding a global carrier, changing the selector, changing reactive `cyber_policy` replay rules, or automatically editing a user's Codex configuration.
- Extending proactive capability lineage through the HTTP bridge, compact, control, chat, Images, Live WebSocket, retry, or reactive-policy machinery.
- Supporting a Daybreak alias that is absent from the current upstream catalog, or running a live provider canary.

## Decisions

### Use a second provider plus a profile file

The published base `config.toml` defines the existing `codex-lb` provider unchanged and a second `codex-lb-daybreak-blue` provider with `env_key = "CODEX_LB_API_KEY"` and the exact static capability header. A separate `daybreak-blue.config.toml` overlay selects that provider. This matches current Codex profile-file semantics, supplies the API-key principal required to trust the signal, and makes activation explicit. Direct Responses WebSocket ingress treats the presence of the internal capability header as requiring validation of that key even when global proxy API-key auth is disabled. HTTP routes apply that same rule through the existing `validate_proxy_api_key` Security dependency rather than a parallel FastAPI identity, so headerless authentication hooks remain intact. Requests without the header retain the existing deployment-level authentication behavior.

Alternative: add the header to the ordinary provider. Rejected because provider headers apply to every request and would incorrectly classify all traffic as requiring trusted-cyber routing.

Alternative: require operators to enable global proxy API-key auth before selecting the Daybreak profile. Rejected because that would change the authentication requirement for ordinary traffic; per-request validation keeps the opt-in boundary narrow.

Alternative: put provider selection in project-local `.codex/config.toml`. Rejected because Codex ignores machine-local provider and profile keys in project configuration.

### Reject capability-bearing unsupported transports before routing

The static provider header follows Codex when a WebSocket attempt falls back to HTTP and on other requests made through the selected provider. External Responses, compact, thread-goal, Codex-control, admission, warmup, files, transcription, Images, and reset-credit consume HTTP routes therefore validate the supplied proxy API key even when deployment-wide authentication is disabled, then return `400 required_capability_transport_unsupported` before non-framework body parsing, model-source lookup, account or ChatGPT usage-identity selection, reservation, fan-out, bridge creation, owner binding, credential decryption, or upstream dispatch. Chat Completions applies the same guard defensively if a provider client reaches that equivalent routing sink. A signed internal bridge request with an appended carrier authenticates and fails closed after signature validation but before legacy-anchor or account routing; legitimate origin forwarding is unchanged because the origin strips the carrier before forwarding. Non-Responses Live WebSockets apply the same authenticate-then-deny contract before owner lookup or upstream connection. Direct Responses WebSocket is the only supported routing transport. Authenticated `/models`, local API-key usage, and reset-credit listing requests remain available because they do not select an upstream account or contact upstream; the carrier-authenticated Codex usage path binds directly to the proxy API-key principal and cannot enter ChatGPT usage validation. The separate WHAM namespace still forwards after the shared `validate_proxy_api_key` gate and does not apply the Responses transport denial, because it is not Codex provider ingress. Headerless requests retain the existing authentication and routing behavior. The HTTP guard uses `400`, not `426`, because Codex treats `426` as a WebSocket-to-HTTP fallback signal.

Alternative: carry proactive capability intent through HTTP selection and failover. Rejected for this bounded fix because HTTP streaming, bridge forwarding, compact, reservations, ownership, retries, and reactive `cyber_policy` compatibility would all need a new strict-versus-reactive invariant to prove that no ordinary account can be selected.

Alternative: allow Images or Live traffic to ignore the carrier because they do not use the Responses WebSocket selector. Rejected because the authenticated carrier is an explicit required capability; silently entering those routes' ordinary account pipelines would downgrade the caller's stated security boundary.

Alternative: rely on `supports_websockets = true` or a feature flag to prevent fallback. Rejected because installed-client verification shows WebSocket attempts can fall back to HTTP and current clients expose no stable fallback-off setting.

### Classify the complete registered proxy ingress

The regression inventory is generated from the routes registered by
`create_app()` and partitions all 45 proxy method/path entries into exactly one
policy group:

- direct carrier routing: the two Responses WebSocket routes;
- authenticate-then-deny: every routing-capable external HTTP route, signed internal bridge defense-in-depth, and the three non-Responses WebSocket routes, including both reset-credit consume surfaces;
- authenticated local handling: Codex and `/v1` models, `/v1/usage`, `GET /v1/reset-credit`, `/api/codex/usage` with and without its trailing slash, and the local Images variations rejection;
- separate namespace: WHAM JWKS.

The `/backend-api/codex/v1/<rest>` middleware alias canonicalizes into those
registered Codex routes and therefore inherits their policy. The inventory
test compares the complete runtime registration against these disjoint groups,
so adding a provider-reachable proxy route without an explicit carrier policy
fails the regression instead of opening another route-by-route review loop.

### Keep the canonical upstream model slug

The Daybreak profile selects `gpt-5.6-sol`. Daybreak Blue may resolve to that model, while access is also bound to the approved identity/workspace or API project and product surface. A distinct provider/profile and the authenticated capability carrier express the routing requirement without teaching codex-lb that a model alias is authorization.

Alternative: infer trusted-cyber intent from `gpt-daybreak-blue-latest` or any other model string. Rejected because model selection alone neither proves Trusted Access nor authenticates routing intent.

### Treat checked-in examples as the client-integration contract

The user-facing examples live under `docs/examples/codex/` and are linked from `docs/client-setup.md`. The integration tests parse those exact TOML files, apply the configured provider contract to real direct Responses WebSocket, HTTP, Images, Live WebSocket, middleware-alias, and signed internal bridge routes, and observe the first-selection or pre-routing denial boundary. An opt-in E2E regression runs an installed Codex binary with temporary `HOME` and `CODEX_HOME`, a fake API key, and a loopback-only network sandbox. It proves sibling-profile resolution, environment-key handling, initial WebSocket header emission, retained HTTP-fallback headers, and no request when the environment key is missing.

Alternative: test a duplicated inline dictionary or documentation prose. Rejected because it could pass after the published configuration drifts.

## Risks / Trade-offs

- **A user selects the Daybreak profile without an approved account surface** -> The carrier grants nothing; canonical selection fails closed when no eligible `security_work_authorized` account exists.
- **A user selects the profile without a valid Codex LB API key** -> Capability ingress requires and validates the dedicated key before selection, independent of the deployment-wide API-key-auth toggle.
- **Codex profile or fallback semantics change** -> The checked-in TOML remains parseable and the opt-in installed-client regression exercises the real loader and first transport attempts; future client changes require updating the configuration contract and regression together.
- **WebSocket is unavailable or another provider route is requested** -> Capability-bearing HTTP and non-Responses WebSockets fail closed with a stable error before routing; direct Responses WebSocket availability must be restored rather than dropping the carrier. This intentionally makes `$imagegen` unavailable inside the Daybreak profile until Images routing can enforce the same capability invariant.
- **Two provider blocks duplicate endpoint settings** -> The duplication is deliberate because Codex providers do not inherit headers safely and isolation is the control that preserves ordinary traffic.
- **The inert regression does not prove current upstream provisioning** -> It proves only client configuration and codex-lb routing behavior; live identity/product-surface approval remains an external prerequisite.

## Migration Plan

Existing users keep the ordinary provider unchanged. To opt in, they add the second provider to user-level `config.toml`, place `daybreak-blue.config.toml` beside it, and explicitly launch with `--profile daybreak-blue`. Rollback removes the profile file and optional second provider; no server, database, or persisted request state changes are required.

## Open Questions

None.
