## Why

Codex clients now advertise GPT-6 Astra and can keep async tool calls pending or steer an active response. The proxy request lifecycle does not yet represent pending async tools or server-created steering continuations correctly.

## What Changes

- Preserve asynchronous tool calls across continuations without synthesizing interrupted-tool outputs for legitimate pending work.
- Track steering submissions and automatic continuations on the originating WebSocket/account, preserving admission, request ownership, usage settlement, terminal handling, cancellation, and explicit reconnect failure semantics.
- Validate Astra reasoning/configuration updates against request and API-key policies while preserving supported input history and documenting compaction constraints.
- Exercise actual proxy entry points with deterministic upstream fixtures, including partial failures and negative controls for existing models.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: async pending-tool continuity, steering ownership, and configuration-update compatibility.
- `api-keys`: Policy and accounting continuity across configuration updates and server-created responses.

## Impact

- Request normalization/policy, WebSocket and HTTP bridge lifecycle, and relevant route tests.
- Scope is Codex clients using existing ChatGPT subscription accounts. No new OpenAI Platform credential or provider setup is required.
- No default model switch, production deployment, or database migration is assumed. Public Responses API documentation is a protocol reference; subscription acceptance must be evidenced separately.
