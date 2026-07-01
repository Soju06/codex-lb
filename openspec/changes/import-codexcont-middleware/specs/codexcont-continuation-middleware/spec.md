## ADDED Requirements

### Requirement: Standalone Responses Middleware Runtime
The repository SHALL provide a standalone Starlette middleware runtime for Responses-compatible POST requests. The runtime MUST load its local configuration from `config.toml`, MUST serve the configured listen paths, and MUST include an example configuration that listens on `127.0.0.1:8787` with `/v1/responses` enabled.

#### Scenario: Run with example configuration
- **WHEN** an operator copies `config.example.toml` to `config.toml`
- **AND** starts the runtime with `uv run python run.py`
- **THEN** the runtime serves the configured host, port, and listen paths

### Requirement: Transparent Passthrough For Non-Continuation Requests
The middleware MUST forward requests without folding when continuation is disabled, the request body is not a JSON object, streaming is not enabled, reasoning is explicitly disabled, or the request is otherwise outside the configured continuation gates.

#### Scenario: Request disables reasoning
- **WHEN** a streaming Responses request explicitly disables reasoning
- **THEN** the middleware forwards the upstream stream without opening hidden continuation rounds

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

### Requirement: Protect Configured Credentials On Header-Selected Upstreams
The middleware MUST strip `Responses-API-Base` before forwarding upstream. When a request-supplied upstream URL is used, the middleware MUST reject requests that would inject configured credentials into that request-supplied URL. The middleware MAY forward caller-supplied authorization headers according to the configured passthrough auth mode.

#### Scenario: Header-selected upstream would receive injected credentials
- **WHEN** a request supplies `Responses-API-Base`
- **AND** the configured auth mode would inject proxy credentials because caller authorization is absent or overridden
- **THEN** the middleware rejects the request with a client error before contacting that upstream
