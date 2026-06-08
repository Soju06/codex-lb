## Why

`codex-lb` is currently centered on Codex/ChatGPT account routing. Agent Load
Balancer needs a provider model that can keep Codex behavior intact while adding
Gemini API routing and a later Google Antigravity CLI connector without mixing
credentials, quota policy, or dashboard state across providers.

## What Changes

- Introduce an agent-provider registry with explicit provider identifiers,
  protocol surfaces, auth modes, quota dimensions, and dashboard sections.
- Expose a read-only provider metadata API for the dashboard and future
  provider-specific settings pages.
- Preserve existing Codex account selection and proxy paths as the only
  production-ready runtime until Gemini account persistence and routing are
  implemented.
- Define Gemini API as the first new proxyable provider surface, with
  Antigravity CLI tracked as a separate harness connector instead of pretending
  it is a raw HTTP model endpoint.
- Record provider capability lifecycle notes and operator actions so the
  dashboard can show Gemini API setup separately from the 2026-06-18 Gemini CLI
  to Antigravity CLI transition.

## Impact

- Existing Codex users keep current routes, settings, quotas, and dashboard
  behavior unchanged.
- Gemini work gains a typed contract before credentials, migrations, or routing
  are introduced.
- Operators get an explicit Antigravity cutover note without ALB claiming that
  `agy` is proxy-ready.
- Review risk is reduced by making provider scope explicit and preventing
  accidental cross-provider quota/dashboard coupling.
