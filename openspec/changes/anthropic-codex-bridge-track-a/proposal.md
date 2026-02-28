## Why

Track A currently accepts `POST /claude/v1/messages`, but request execution still follows the Anthropic SDK transport path. That does not satisfy the proxy objective for this rollout: Claude-compatible clients should be able to call the Anthropic Messages API surface while codex-lb executes the request on Codex (`gpt-5.3-codex`) through the existing OpenAI proxy stack.

## What Changes

- Bridge `/claude/v1/messages` to the Codex proxy path (`ProxyService.stream_responses`) instead of Anthropic SDK upstream execution.
- Translate Anthropic Messages payloads into OpenAI-compatible responses requests, including messages, tools, and tool-choice semantics used by Claude Code.
- Translate OpenAI chat completion and error outputs back into Anthropic-compatible message/error envelopes.
- Keep `/claude-sdk/v1/messages` on the SDK transport for comparison and fallback testing.
- Add lightweight Claude Desktop bootstrap compatibility endpoints (`/api/bootstrap`, `/api/desktop/features`, `/api/event_logging/batch`) so custom deployment mode can initialize cleanly.

## Non-Goals

- No direct Anthropic upstream dependency for `/claude/v1/messages` in Track A.
- No new account onboarding flows in this change.
- No model auto-discovery from Anthropic model IDs; Track A is forced to Codex target model.

## Capabilities

### Modified Capabilities

- `anthropic-messages-compat`: `/claude/v1/messages` transport and response behavior.

## Impact

- **Code**:
  - `app/modules/anthropic/api.py`
  - `app/modules/anthropic/codex_compat.py`
  - `app/main.py`
- **Tests**:
  - `tests/integration/test_anthropic_messages_api.py`
  - `tests/unit/test_anthropic_codex_compat.py`
