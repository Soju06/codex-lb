## Why

The bounded `/v1` prompt-cache affinity fix stopped permanent account pinning, but operators still cannot tune the freshness window from the dashboard, inspect which sticky mappings exist, or proactively remove stale prompt-cache rows. Stale prompt-cache mappings also linger until the next read for that exact key.

Issue `#172` explicitly asked for operator control and sticky-session visibility without regressing Codex CLI compatibility. The next step is to make prompt-cache affinity operationally manageable while preserving durable backend `session_id` routing and dashboard sticky-thread behavior.

## What Changes

- Persist the OpenAI prompt-cache affinity TTL in dashboard settings and expose it in the Settings UI.
- Store an explicit sticky-session kind so prompt-cache mappings can be distinguished from durable Codex session and sticky-thread mappings.
- Add dashboard APIs and UI for listing sticky mappings, deleting one mapping, and purging stale prompt-cache mappings.
- Add a background cleanup loop that proactively deletes only stale prompt-cache mappings.

## Impact

- Operators can tune prompt-cache affinity without restart.
- Durable Codex session affinity and sticky-thread routing remain unchanged.
- Prompt-cache mappings remain bounded, observable, and purgeable instead of accumulating silently.
