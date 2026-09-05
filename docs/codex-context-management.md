# Experimental Codex history and notes

Source of truth: [Codex context management specification](../openspec/specs/codex-context-management/spec.md).

This compatibility path supports native Codex notes and history with an account pool. OpenAI's backend stores the content. codex-lb remembers which account owns the notes and which accounts have handled each task.

## Pool behavior

Use the same valid proxy API key for Responses and context tools, and enable the existing global API-key authentication setting. Unscoped keys and keys assigned to one or several accounts work. Each account still needs access to the model and experimental backend feature.

A task's first observed inference dispatch, or first context operation, fixes its notes owner. Inference may rotate to another eligible account while notes stay on their owner. Quota exhaustion alone does not prevent trying a notes operation, and successful notes access does not reset the account's inference quota. If the notes owner is deleted, paused or unavailable, notes fail explicitly until that owner is usable again.

History queries contact all recorded participants, with a maximum of 32 accounts and four concurrent calls. One failed participant fails the entire query. Results remain encrypted; the model combines the partitions. Exact global ordering, deduplication and pagination are therefore not guaranteed by the proxy.

The client sends one encrypted tool-result string. codex-lb wraps native results in its own authenticated container, then restores native ciphertext and images before the next inference request. Only verified context tool outputs are eligible for cross-account replay; arbitrary encrypted reasoning, stored references and incomplete tool exchanges retain existing restrictions.

## Isolated evaluation

Run a separate build with a new empty data volume and loopback port, such as `127.0.0.1:2456`. Import eligible accounts, enable API-key authentication and create a test key with access to the pool. Use a new task so participation tracking starts before its first inference request.

Codex rust-v0.153.1 requires the provider display name `OpenAI` and a ChatGPT login for the history/notes extension. Explicit token-budget options enable the extension with a proxy bearer token. These under-development client settings may change in future versions.

Save a separate `~/.codex/codex-context-test.config.toml` with permissions `0600`. Keep the key out of Git.

```toml
model = "gpt-6-astra"
model_provider = "codex-context-test"

[model_providers.codex-context-test]
name = "OpenAI"
base_url = "http://127.0.0.1:2456/backend-api/codex"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "REPLACE_WITH_POOL_PROXY_KEY"
supports_websockets = true

[features.context_management]
experimental_mode = true

[features.token_budget]
enabled = true
use_history_notes_extension = true
```

Start `codex --profile codex-context-test`. Keep the existing ChatGPT login. The default provider configuration remains separate from this profile.

## Verification

1. Check `/health`, the authenticated models endpoint and ordinary inference.
2. Verify actual `notes.write_file`, `notes.read_file` and history operations with synthetic markers.
3. Rotate inference to another account, read the existing note and query both history partitions.
4. Restart the test proxy, use `new_context` and recover the markers from notes and history.
5. Verify another API key cannot use the same session, and that removing a required account from the key scope causes an explicit failure.

Automated tests cover opaque route dispatch, HTTP streaming, HTTP bridging, native WebSockets, private errors, key isolation, concurrent ownership, partial-failure cancellation and SQLite/PostgreSQL migration behavior. Live CLI validation used two accounts and a simulated quota rejection before upstream inference, without exhausting real quota.

## Persistence and failures

Preserve both the database and existing `encryption.key`. Startup migrations create the ownership tables automatically. Replicas must share both to read the same context. Transparent retries through cross-replica forwarding and restored durable request state remain unverified and may preserve account ownership rather than rotate. The live restart test resumed through the normal client request path.

A missing or invalid key returns `401`. Context requests return `409` if global API-key authentication is disabled, `403` for identity/scope conflicts, and `503` for an unavailable owner or incomplete history query. Their error bodies use the generic code `context_backend_unavailable` to avoid exposing private upstream details. Invalid Responses containers produce `400` or `403` with `context_result_invalid`.

Timeouts and ambiguous writes are not retried. An explicit authentication rejection may refresh and retry once on the same owner. There is no account-to-account notes copy or new dashboard setting.

Stop the isolated proxy and exit the test task to end evaluation. Downgrading the database removes ownership records; begin new context tasks after a downgrade/re-upgrade. Keep old backups and the associated encryption key if those tasks need to be recovered.
