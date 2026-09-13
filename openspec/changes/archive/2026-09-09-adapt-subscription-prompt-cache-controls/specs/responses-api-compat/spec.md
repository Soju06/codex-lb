# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Subscription Responses adapts unsupported explicit prompt-cache controls

The proxy MUST omit public explicit prompt-cache controls from an HTTP Responses
request routed to the Codex subscription upstream. This applies to
`prompt_cache_options` and `prompt_cache_breakpoint` on a supported prompt
content block. It MUST preserve the prompt content and ordering and MUST
continue forwarding a client-supplied `prompt_cache_key` unchanged.

A successful HTTP response for such a request MUST include
`X-Codex-LB-Prompt-Cache-Mode: subscription-implicit`, because subscription
implicit caching and account affinity do not provide the exact explicit-prefix
semantics requested by the client. The proxy MUST NOT include that downgrade
header when the request is routed to an OpenAI-compatible model source, and the
model-source wire payload MUST preserve the explicit controls unchanged.

#### Scenario: Subscription request falls back to implicit caching

- **GIVEN** a `/v1/responses` request contains a `prompt_cache_key`,
  `prompt_cache_options`, and an explicit breakpoint on an `input_text` block
- **WHEN** the request is routed to a subscription account
- **THEN** the upstream subscription payload omits `prompt_cache_options` and
  the breakpoint
- **AND** preserves the input text, input order, and `prompt_cache_key`
- **AND** a successful response reports
  `X-Codex-LB-Prompt-Cache-Mode: subscription-implicit`

#### Scenario: Model source preserves public explicit-cache semantics

- **GIVEN** the same `/v1/responses` request is routed to an OpenAI-compatible
  model source
- **THEN** the model-source payload retains `prompt_cache_options`, every
  explicit breakpoint, and `prompt_cache_key`
- **AND** the response does not report a subscription implicit fallback
