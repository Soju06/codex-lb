## Context

The direct Responses WebSocket ingress already accepts one authenticated `trusted_cyber` carrier and applies the existing `security_work_authorized` selector constraint before opening an upstream connection. The standard Codex client example defines only the ordinary `codex-lb` provider, so Codex Desktop/CLI orchestration has no explicit opt-in configuration that sends the carrier. Provider and profile selection are machine-local Codex settings; current Codex versions ignore them in project-local `.codex/config.toml` files.

## Goals / Non-Goals

**Goals:**

- Publish a machine-local Codex configuration with separate ordinary and Daybreak Blue providers.
- Make `--profile daybreak-blue` select the Daybreak provider and canonical `gpt-5.6-sol` model.
- Send the exact trusted-cyber carrier on every Daybreak provider request, including the first request, while leaving ordinary provider requests unchanged.
- Prove the checked-in configuration against the real direct-WebSocket capability-ingress and first-selection seam without external requests or credentials.

**Non-Goals:**

- Granting Trusted Access, discovering approved identities, or deriving authorization from a model slug, prompt, skill, user agent, or task content.
- Adding a global carrier, changing the selector, changing reactive `cyber_policy` replay rules, or automatically editing a user's Codex configuration.
- Supporting a Daybreak alias that is absent from the current upstream catalog, or running a live provider canary.

## Decisions

### Use a second provider plus a profile file

The published base `config.toml` defines the existing `codex-lb` provider unchanged and a second `codex-lb-daybreak-blue` provider with `env_key = "CODEX_LB_API_KEY"` and the exact static capability header. A separate `daybreak-blue.config.toml` overlay selects that provider. This matches current Codex profile-file semantics, supplies the API-key principal required to trust the signal, and makes activation explicit.

Alternative: add the header to the ordinary provider. Rejected because provider headers apply to every request and would incorrectly classify all traffic as requiring trusted-cyber routing.

Alternative: put provider selection in project-local `.codex/config.toml`. Rejected because Codex ignores machine-local provider and profile keys in project configuration.

### Keep the canonical upstream model slug

The Daybreak profile selects `gpt-5.6-sol`. Daybreak Blue may resolve to that model, while access is also bound to the approved identity/workspace or API project and product surface. A distinct provider/profile and the authenticated capability carrier express the routing requirement without teaching codex-lb that a model alias is authorization.

Alternative: infer trusted-cyber intent from `gpt-daybreak-blue-latest` or any other model string. Rejected because model selection alone neither proves Trusted Access nor authenticates routing intent.

### Treat checked-in examples as the client-integration contract

The user-facing examples live under `docs/examples/codex/` and are linked from `docs/client-setup.md`. The inert integration test parses those exact TOML files, resolves the selected provider, applies its configured headers to the real direct Responses WebSocket route, and observes the first account-selection constraint. This keeps documentation and tested behavior on one source artifact.

Alternative: test a duplicated inline dictionary or documentation prose. Rejected because it could pass after the published configuration drifts.

## Risks / Trade-offs

- **A user selects the Daybreak profile without an approved account surface** -> The carrier grants nothing; canonical selection fails closed when no eligible `security_work_authorized` account exists.
- **A user selects the profile without a valid Codex LB API key** -> Capability ingress rejects the untrusted signal before selection; the guide makes the dedicated key prerequisite explicit.
- **Codex profile semantics change** -> The checked-in TOML remains parseable and the docs cite current machine-local profile-file behavior; future client changes require updating the configuration contract and regression together.
- **Two provider blocks duplicate endpoint settings** -> The duplication is deliberate because Codex providers do not inherit headers safely and isolation is the control that preserves ordinary traffic.
- **The inert regression does not prove current upstream provisioning** -> It proves only client configuration and codex-lb routing behavior; live identity/product-surface approval remains an external prerequisite.

## Migration Plan

Existing users keep the ordinary provider unchanged. To opt in, they add the second provider to user-level `config.toml`, place `daybreak-blue.config.toml` beside it, and explicitly launch with `--profile daybreak-blue`. Rollback removes the profile file and optional second provider; no server, database, or persisted request state changes are required.

## Open Questions

None.
