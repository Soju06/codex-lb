## Provider Model

Agent Load Balancer will treat each agent backend as an `agent_provider`.
Provider-owned code should hold protocol/auth specifics while shared routing
logic consumes typed provider metadata. The central proxy should dispatch by
provider and request surface, not infer behavior from model-name strings alone.

Initial provider ids:

- `codex`: existing ChatGPT/Codex account pool and current proxy routes.
- `gemini`: Google Gemini API and Vertex-compatible Gemini traffic.
- `antigravity`: Google Antigravity CLI harness profiles and future `agy`
  execution sessions.

Initial protocol surfaces:

- `codex_chatgpt`: existing Codex/ChatGPT upstream behavior.
- `gemini_api`: Gemini Developer API over API key.
- `vertex_ai`: Vertex AI Gemini surface, planned after Developer API.
- `antigravity_cli`: Google Antigravity CLI harness connector. This is not
  OpenAI-proxy-ready in V1 because it has CLI/keyring/session behavior rather
  than a simple load-balanced HTTP endpoint.

Current Google guidance says individual-tier Gemini CLI traffic stops being
served on 2026-06-18 and Antigravity CLI (`agy`) is the migration target.
Antigravity CLI stores settings under `~/.gemini/antigravity-cli/`, uses shared
Antigravity harness/session behavior, and relies on local auth/keyring
semantics. ALB therefore treats it as a harness connector, not as a drop-in HTTP
proxy backend. The dashboard can register Antigravity CLI profiles as provider
accounts with `cli_keyring` auth mode and run a dashboard-authenticated
noninteractive `agy --print` probe through provider routing.

## Dashboard Shape

The dashboard should eventually show:

- A combined overview across providers.
- A Codex section preserving current accounts, quotas, routing settings, usage,
  reports, and logs.
- A Gemini section with separate accounts/API keys, rate-limit/quota drains,
  provider-specific model catalog, usage, reports, and settings.
- An Antigravity section with separate CLI harness profiles, provider routing
  settings, quota windows, preflight state, and harness execution.

The first implementation slice exposes read-only provider metadata so frontend
work can render separate sections without hardcoding provider availability.

Gemini Developer API traffic is exposed both through the provider-specific
`/v1/gemini/chat/completions` route and through the normal OpenAI-compatible
`/v1/chat/completions` route when the effective model starts with `gemini-`.
Codex models continue through the existing proxy path.

OpenAI-compatible `/v1/models` includes provider-owned Gemini Developer API
text-output models alongside Codex registry models. Gemini entries carry
provider/protocol/lifecycle metadata, token limits, multimodal input
capabilities, streaming support, and obey the same API-key allowed/enforced
model filters as Codex entries. Dashboard `/api/models` also includes these
Gemini entries so provider-aware UI surfaces can discover Codex and Gemini
models from one catalog.

Antigravity profiles use the provider-account table without storing API-key
material. The external account id is the local `agy` profile/session identity.
Profiles are active routing candidates for the harness, but raw `/v1` proxy
requests are not routed to Antigravity CLI. Antigravity uses the shared
provider-routing settings and quota-window APIs so dashboard controls stay
provider-scoped instead of sharing Gemini state.
Provider metadata marks Antigravity as a foundation harness surface once the
dashboard-authenticated agy --print adapter is available, while keeping
proxyable=false so clients do not mistake it for an HTTP model endpoint.

## Review Trapdoors Applied

- OpenSpec comes first for provider/API/dashboard/operator-contract changes.
- Provider settings must be persisted before routing code reads them.
- Preflight must mirror real routing once Gemini selection exists.
- Quota drain policies must stay behind budget-safety gates.
- Provider credentials and quotas must not bleed across Codex and Gemini.
