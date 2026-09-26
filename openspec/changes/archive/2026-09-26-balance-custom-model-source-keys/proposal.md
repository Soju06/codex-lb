## Why

Multiple credentials for the same custom Codex model currently require separate model sources but always select the first matching source. Operators need these sources to share Responses traffic and recover from an unavailable credential without changing clients.

## What Changes

- Automatically balance unanchored Responses requests across enabled, authorized sources serving the same public model.
- Prefer fewer in-flight requests and rotate equally loaded sources; skip saturated and cooling sources.
- Retry eligible pre-response failures across distinct sources, with bounded attempts, independent reservation settlement and source-local aliases.
- Keep known response anchors on their recorded source, reject ambiguous/unavailable ownership and preserve subscription/file exclusions.
- Document replica-local coordination, cooldowns, conservative retry limits and the unchanged client configuration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: Automatic source pooling for the Responses routes used by Codex.

## Impact

Model-source candidate lookup, Responses dispatch, transient selection state, ownership queries and route-level regression tests. No database migration, new environment setting, client changes or dashboard form changes. Chat Completions, Embeddings, Audio and subscription-overflow policies retain their existing behavior.
