## ADDED Requirements

### Requirement: Standalone Responses Middleware Runtime
The repository SHALL provide a standalone Starlette middleware runtime for Responses-compatible POST requests. The runtime MUST load its local configuration from `config.toml`, MUST serve the configured listen paths, and MUST include an example configuration that listens on `127.0.0.1:8787` with `/v1/responses` enabled.

#### Scenario: Run with example configuration
- **WHEN** an operator copies `config.example.toml` to `config.toml`
- **AND** starts the runtime with `uv run python run.py`
- **THEN** the runtime serves the configured host, port, and listen paths

### Requirement: Passive Codex-LB Responses Stream Integration
codex-lb SHALL apply CodexCont continuation folding by default to Responses-compatible HTTP streaming requests that enter the normal codex-lb Responses stream service path and do not fail the continuation gates. The integrated fold MUST be controlled by `CODEX_LB_CODEX_CONTINUATION_ENABLED`.

#### Scenario: Normal codex-lb Responses stream is continuation eligible
- **WHEN** a codex-lb HTTP Responses stream is created while `CODEX_LB_CODEX_CONTINUATION_ENABLED=true`
- **AND** reasoning is not explicitly disabled
- **THEN** codex-lb applies continuation folding before yielding the downstream SSE stream

### Requirement: Hidden Continuation Rounds Reuse Selected Upstream Contract
The integrated codex-lb fold MUST open hidden continuation rounds through the same selected upstream account, auth headers, upstream route, Codex client/session, transport override, and SDK-contract normalization as the visible round. Hidden continuation rounds MUST NOT re-enter account selection as separate user-visible stream requests and MUST NOT create independent API-key reservations.

#### Scenario: Truncated codex-lb stream opens hidden round
- **WHEN** a codex-lb stream detects a continuation-eligible truncation fingerprint
- **THEN** the hidden round uses the already selected account and route
- **AND** codex-lb presents the folded rounds as one downstream stream

### Requirement: HTTP Bridge Does Not Bypass Continuation Folding
When the HTTP session bridge is enabled and `CODEX_LB_CODEX_CONTINUATION_BYPASS_HTTP_BRIDGE=true`, codex-lb MUST route continuation-eligible HTTP Responses streams through the standard stream path instead of the HTTP bridge so the passive continuation fold is applied.

#### Scenario: Bridge would otherwise handle continuation-eligible stream
- **WHEN** the HTTP session bridge is enabled
- **AND** a stream is continuation eligible
- **THEN** codex-lb bypasses the bridge and uses the standard stream path

### Requirement: Transparent Passthrough For Non-Continuation Requests
The middleware and integrated codex-lb path MUST forward requests without continuation folding when continuation is disabled, the request body is not a JSON object, reasoning is explicitly disabled, or the request is otherwise outside the configured continuation gates.

#### Scenario: Request disables reasoning
- **WHEN** a streaming Responses request explicitly disables reasoning
- **THEN** the request is forwarded without opening hidden continuation rounds

### Requirement: Fold Detected Reasoning-Truncation Streams
For streaming Responses requests that pass the continuation gates, the middleware MUST inspect terminal upstream usage. When `usage.output_tokens_details.reasoning_tokens` matches `truncation_step * n - 2`, encrypted reasoning content is available, and configured caps allow continuation, the middleware MUST discard tentative final output from the truncated round, append the prior reasoning plus the configured continuation marker to the next upstream request, and open another upstream streaming round.

#### Scenario: Truncated round continues
- **WHEN** an upstream terminal event reports a matching reasoning-token truncation fingerprint
- **AND** configured continuation caps allow another round
- **THEN** the downstream stream does not emit the truncated round's tentative final output
- **AND** the middleware opens a continuation round with prior encrypted reasoning preserved

### Requirement: Reconstruct Downstream Stream State
The middleware MUST present folded upstream rounds as one coherent downstream SSE stream. It MUST rewrite downstream sequence and output indexes consistently, MUST flush only the final accepted message or function-call output, and MUST include proxy metadata describing hidden rounds, summed upstream usage, and stopped reasons when applicable.

#### Scenario: Final round succeeds
- **WHEN** a folded request reaches a non-truncated terminal round
- **THEN** the downstream stream includes the final round output
- **AND** the reconstructed terminal response includes proxy round and billed-usage metadata

#### Scenario: Folded codex-lb stream settles usage
- **WHEN** a folded codex-lb stream returns agent-facing `response.usage` and `metadata.proxy_billed_usage`
- **THEN** downstream clients receive the agent-facing `response.usage`
- **AND** codex-lb API-key settlement and request logs use `metadata.proxy_billed_usage`

### Requirement: Protect Configured Credentials On Header-Selected Upstreams
The middleware MUST strip `Responses-API-Base` before forwarding upstream. When a request-supplied upstream URL is used, the middleware MUST reject requests that would inject configured credentials into that request-supplied URL. The middleware MAY forward caller-supplied authorization headers according to the configured passthrough auth mode.

#### Scenario: Header-selected upstream would receive injected credentials
- **WHEN** a request supplies `Responses-API-Base`
- **AND** the configured auth mode would inject proxy credentials because caller authorization is absent or overridden
- **THEN** the middleware rejects the request with a client error before contacting that upstream
