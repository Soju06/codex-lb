# Implementation notes

Healthy native HTTP requests use normal policy. The proxy cannot infer every
client-local WebSocket failure from HTTP alone; it uses its existing 60-second
upstream-connect failure marker as concrete failure evidence. Operator HTTP
pins, image and size bypasses remain effective. No new retry/session registry.

History-only locality is soft, scoped by the bridge's full API-key identifier,
and hashes the complete first user item plus instructions and model. No client
prompt cache field is overwritten. Identical initial prompts may share an idle
connection, but neither histories nor response anchors are merged; the complete
request is sent each time. Existing hard-continuity paths retain their guarded
incremental replay. Conversation IDs get their own hashed locality and are never
combined with an injected previous_response_id.

Chat keeps the existing stream conversion/usage/error/cleanup pipeline and uses
the bridge only after the source-routing branch. Backend stream=false retains
its native non-streaming upstream contract. No claimed latency percentage:
connection reuse is measured separately from admission and successful transport.
