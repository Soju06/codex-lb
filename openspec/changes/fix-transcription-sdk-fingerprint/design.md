## Context

Responses and websocket upstream forwarding already classify native Codex requests and rewrite non-native clients to a canonical `codex_cli_rs` persona. The transcription header builder deliberately uses a small header set, but currently copies an inbound user agent without classification. An OpenAI Node SDK therefore reaches ChatGPT `/transcribe` with its Stainless fingerprint.

## Decision

Classify transcription traffic with the existing native Codex predicate. For non-native traffic, retain the transcription path's minimal allowlist and apply the established non-native fingerprint normalizer. This supplies canonical `User-Agent`, `originator`, and `version`, strips known OpenAI SDK fields, and leaves authentication/account ownership behavior intact. Native traffic retains its existing minimal forwarding behavior and inbound user agent.

## Validation

Use an in-process fake `/transcribe` server to capture actual multipart upstream headers. Exercise `file` plus `model=gpt-4o-transcribe` on the public compatibility route, assert its JSON response shape, selected account authorization/account id, canonical non-native fingerprint, absence of Stainless fields, and generated multipart boundary. Keep direct native-header coverage.
