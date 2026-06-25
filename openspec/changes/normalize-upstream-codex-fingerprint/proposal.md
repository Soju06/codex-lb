# Normalize non-native upstream requests to the Codex CLI client fingerprint

## Problem

On production codex-lb (`gpt-5.5`, priority-tier API keys), a large share of
requests fail with `server_is_overloaded` ("Our servers are currently
overloaded. Please try again later."). Measured over a 12h prod window:

- `gpt-5.5` overall: **8.94%** of requests (1010/11293) surfaced
  `server_is_overloaded`.
- Split by the tier the ChatGPT backend actually applied
  (`actual_service_tier`): `default` tier = **0.18%** failures, but `auto`
  tier = **85.04%** failures. ~98.5% of all overload events were `auto`-tier
  requests.
- `auto` is not what clients requested. Clients sent
  `requested_service_tier=priority`; the backend **downgraded** a fraction of
  them to `auto`, and those downgraded requests are the ones that overload.

The downgrade rate is not uniform across clients sending the same
`priority` tier. Split by transport + client User-Agent over a 3h window:

| transport | client User-Agent | priority reqs | downgraded→auto |
|-----------|-------------------|---------------|-----------------|
| http      | `OpenAI/Python`   | 2231          | **20.3%**       |
| websocket | `codex-tui` / `codex_exec` / `Codex Desktop` | ~2000 | **2.8–6.0%** |
| http      | `OpenAI/Python` (other key, woonggi) | 21 | **28.6%** |

The same `OpenAI/Python` http path downgrades at ~20–29% regardless of which
API key sends it, while native Codex clients (websocket) stay at ~3–6%. This
is a **client-fingerprint** effect, not a per-key or per-account effect.

Root cause: the ChatGPT/Codex backend whitelists first-party Codex
originators (`codex_cli_rs`, `codex_vscode`, `codex_sdk_ts`, or anything
starting with `Codex`; see
`openai/codex` `codex-rs/login/src/auth/default_client.rs`
`DEFAULT_ORIGINATOR` and `codex-rs/core/src/client.rs` `add_originator_header`).
Requests carrying a non-first-party fingerprint (e.g. the `openai` Python SDK's
`User-Agent: OpenAI/Python x.y.z` plus `x-openai-client-*` headers) are treated
as second-class API traffic: their requested `priority` tier is more likely to
be downgraded to `auto`, which the backend sheds first under load.

codex-lb forwards the inbound client's headers verbatim on the http path
(`app/core/clients/proxy.py::_build_upstream_headers` does `headers =
dict(inbound)` with no User-Agent / originator normalization), so a
non-native client's fingerprint reaches the backend unchanged.

## Solution

Normalize **non-native http** upstream requests to the Codex CLI
(`codex_cli_rs`) client fingerprint inside
`app/core/clients/proxy.py::_build_upstream_headers`, matching what the
official Codex CLI sends (`get_codex_user_agent()` in
`codex-rs/login/src/auth/default_client.rs`):

1. Set `User-Agent` to
   `codex_cli_rs/<version> (<os>; <arch>) <terminal>`, where `<version>` comes
   from the existing `CodexVersionCache` (GitHub releases → npm → stale cache →
   `model_registry_client_version` settings default) and `<os>/<arch>/<terminal>`
   come from new configurable settings (defaults
   `Mac OS 26.5.0` / `arm64` / `iTerm.app/3.6.10`).
2. Strip SDK-only fingerprint headers (`x-openai-client-version`,
   `x-openai-client-os`, `x-openai-client-arch`, `x-openai-client-id`,
   `x-openai-client-user-agent`).
3. Do **not** add an `originator` header: the real Codex CLI omits the
   `originator` header when the originator equals the default (`codex_cli_rs`)
   and lets the backend read it from the `User-Agent` prefix. Strip any
   inbound `originator` header so no SDK-supplied value leaks through.
4. Use PascalCase `ChatGPT-Account-Id` for the account header, matching Codex
   CLI (`codex-rs/backend-client/src/client.rs`).

A request is **native** (left untouched) when its User-Agent already begins
with a known Codex client token (`codex_cli_rs`, `codex-tui`, `codex_exec`,
`codex_vscode`, `Codex Desktop`, or `Codex ...`) **or** it already carries
native Codex transport headers (`originator` in the native set, or any
`x-codex-*` stream header). websocket requests are unaffected because they use
`_build_upstream_websocket_headers` and already connect as native Codex
clients.

## Why this is correct as a behavior change

- `outbound-http-clients` already promises stable, persona-aware outbound
  headers (the OAuth authorize originator persona requirement). Normalizing the
  proxy's upstream client fingerprint to the same first-party Codex persona is
  consistent with that capability's existing intent.
- No client can depend on its raw `User-Agent` / `x-openai-client-*` reaching
  the ChatGPT backend: those headers are an upstream-private transport detail,
  not part of any documented codex-lb request/response contract. codex-lb's own
  `request_logs.useragent` keeps the original client value for observability;
  only the upstream wire fingerprint changes.
- Native Codex clients are explicitly excluded, so the change cannot regress
  the already-healthy native path.

## Changes

### Spec deltas
- `outbound-http-clients`: ADD a Requirement covering non-native upstream http
  request fingerprint normalization (User-Agent, originator omission, SDK
  header stripping, account-header casing, native-client exemption).

### Code
- `app/core/clients/codex_version.py::CodexVersionCache` — add a synchronous
  `cached_version_or_default()` read path (no `await`, no network) for the
  hot header-build path; background refresh stays on the existing async
  `get_version()` already called every model-registry refresh cycle.
- `app/core/clients/proxy.py` — add `build_codex_user_agent(version)` helper
  and a `_normalize_non_native_upstream_fingerprint()` step; call it from
  `_build_upstream_headers` for non-native http requests. Add native-client
  detection by User-Agent prefix combined with the existing
  `_has_native_codex_transport_headers`.
- `app/core/config/settings.py` — add `codex_fingerprint_os`,
  `codex_fingerprint_arch`, `codex_fingerprint_terminal` settings (defaults
  `Mac OS 26.5.0` / `arm64` / `iTerm.app/3.6.10`).

### Tests
- `tests/unit/test_proxy_upstream_fingerprint.py` (new) — non-native http UA
  rewritten to `codex_cli_rs/...`; native UA + websocket unchanged;
  `x-openai-client-*` and inbound `originator` stripped; no `originator` header
  added; PascalCase `ChatGPT-Account-Id`; version sourced from cache with
  settings-default fallback.
- `tests/unit/test_codex_version_cache.py` — `cached_version_or_default()`
  returns the cached version when present and the settings default when empty,
  with no network/await.

## Out of scope

- Changing websocket upstream headers (`_build_upstream_websocket_headers`).
- Changing native Codex client requests.
- Per-key rate limiting, account-pool sizing, or retry/backoff policy for
  `auto`-tier overloads (separate concerns).
- Auto-detecting the host OS/arch for the fingerprint (intentionally fixed,
  operator-overridable settings).
