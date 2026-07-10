## ADDED Requirements

### Requirement: Ultra reasoning effort is aliased to max on the upstream wire

The proxy MUST forward any outbound upstream Responses payload whose `reasoning.effort` resolves to `ultra` — whether requested by the client or injected by API-key reasoning enforcement — with `reasoning.effort: "max"`. `ultra` is a client-plane reasoning effort: GPT-5.6 Sol and Terra advertise it
in their catalog entries, but the reference Codex client rewrites it to `max`
before building the upstream Responses request
(`reasoning_effort_for_request` in codex-rs `core/src/client.rs` at release
rust-v0.144.1); its additional effect (proactive multi-agent mode) is purely
client-side. Source-routed chat-completions
payloads with an enforced `ultra` effort MUST likewise forward `max`. `max`
and `xhigh` MUST be forwarded verbatim (no `max` → `xhigh` aliasing exists
upstream).

#### Scenario: Client-requested ultra forwards as max

- **WHEN** a client sends a Responses request for `gpt-5.6-sol` with `reasoning: {"effort": "ultra"}`
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Enforced ultra forwards as max

- **GIVEN** an API key configured with `enforcedReasoningEffort: "ultra"`
- **WHEN** a request is proxied with that API key
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Max is forwarded verbatim

- **WHEN** a client sends a Responses request with `reasoning: {"effort": "max"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "max"`
