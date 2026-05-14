## Context

OpenAI Platform Responses usage includes `input_tokens` and `cached_tokens`, and codex-lb already parses those values for request logs. The missing operational loop is proactive alerting when a fallback key repeatedly receives uncached input after Platform fallback activates.

The alert destination is the existing `slack-cache-alert-proxy`, which accepts a POST body and formats the Slack message. codex-lb must never send the raw Platform API key or fail a proxied response because the alert proxy is unavailable.

## Goals / Non-Goals

**Goals:**

- Detect repeated uncached Platform requests with the operator-requested 7-request window and 4-miss threshold.
- Identify the affected Platform credential by last 4 characters only.
- Cover non-streaming, streaming, and compact Platform Responses paths that expose usage.
- Keep alert delivery best-effort with bounded timeout and cooldown.

**Non-Goals:**

- Changing the fallback routing decision.
- Persisting alert state in the database.
- Modifying `slack-cache-alert-proxy`.
- Sending full API keys, request bodies, prompts, or token counts to Slack.

## Decisions

- Use an in-memory rolling window keyed by API-key suffix. This keeps the change schema-free and sufficient for one-process operational alerting; the existing request logs remain the durable source for post-hoc investigation.
- Trigger only when `input_tokens > 0` and `cached_input_tokens` is missing or zero. Requests without input tokens do not represent prompt-cache misses.
- POST the suffix as a plain text body to the configured proxy URL. The downstream proxy already owns Slack formatting, so codex-lb should not duplicate that message contract.
- Add a cooldown per suffix after an alert fires. Without a cooldown, every subsequent uncached request can repeatedly satisfy the same 4-of-7 window and spam Slack.
- Keep configuration optional. When no alert proxy URL is configured, observation remains a no-op.

## Risks / Trade-offs

- Multi-replica deployments keep independent windows -> Alerting may be delayed or duplicated across replicas; cooldown limits duplicates and request logs remain authoritative.
- Suffix-only identity can collide across different keys -> This is the user-requested privacy boundary; operators can correlate suffixes through their secret inventory.
- Alert proxy failure can hide a live issue -> Delivery failures are logged, and request logs still carry cache metrics for manual inspection.
