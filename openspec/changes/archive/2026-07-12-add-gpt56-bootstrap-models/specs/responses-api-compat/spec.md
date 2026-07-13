## ADDED Requirements

### Requirement: Ultra reasoning effort is aliased to max on the upstream wire

The proxy MUST forward any outbound upstream Responses payload whose `reasoning.effort` resolves to `ultra` — whether requested by the client or injected by API-key reasoning enforcement — with `reasoning.effort: "max"`. `ultra` is a client-plane reasoning effort: GPT-5.6 Sol and Terra advertise it
in their catalog entries, but the reference Codex client rewrites it to `max`
before building the upstream Responses request
(`reasoning_effort_for_request` in codex-rs `core/src/client.rs` at release
rust-v0.144.1); its additional effect (proactive multi-agent mode) is purely
client-side. Source-routed chat-completions
payloads with an enforced `ultra` effort MUST likewise forward `max`. Code
paths that build upstream Responses payloads directly instead of passing
through the proxy request-policy rewrite — such as automation compact pings —
MUST apply the same aliasing before dispatch, while persisted automation
configuration and run history keep the configured client-plane `ultra` value.
`max`
and `xhigh` MUST be forwarded verbatim (no `max` → `xhigh` aliasing exists
upstream).

#### Scenario: Client-requested ultra forwards as max

- **WHEN** a client sends a Responses request for `gpt-5.6-sol` with `reasoning: {"effort": "ultra"}`
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Enforced ultra forwards as max

- **GIVEN** an API key configured with `enforcedReasoningEffort: "ultra"`
- **WHEN** a request is proxied with that API key
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Automation compact ping with ultra dispatches max

- **GIVEN** an automation configured with model `gpt-5.6-sol` and reasoning effort `ultra`
- **WHEN** an automation run dispatches its compact ping upstream
- **THEN** the dispatched compact payload uses `reasoning.effort: "max"`
- **AND** the stored automation run history keeps the configured `ultra` effort

#### Scenario: Max is forwarded verbatim

- **WHEN** a client sends a Responses request with `reasoning: {"effort": "max"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "max"`

## MODIFIED Requirements

### Requirement: Cursor GPT-5 model aliases normalize to canonical slugs

For Responses proxy traffic, the service MUST recognize Cursor-style GPT-5 model aliases formed by appending known suffix tokens
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs, including the GPT-5.6
personality slugs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged; `ultra` and `max` are not suffix tokens (they are not effort levels
every GPT-5-family base supports — `gpt-5.6-luna` advertises no `ultra`), so
labels such as `gpt-5.6-sol-ultra` pass through unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 personality alias normalizes reasoning and service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-extra-high-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.6-sol"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 ultra-suffixed label is not rewritten

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-ultra"`
- **THEN** the forwarded upstream request keeps `model: "gpt-5.6-sol-ultra"` unchanged
