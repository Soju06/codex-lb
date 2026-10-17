## Why

OpenAI Node SDK transcription requests identify themselves with an `OpenAI/JS` user agent, `x-stainless-*` headers, and `x-openai-client-*` headers. The transcription forwarding path forwards the `OpenAI/JS` user agent and `x-openai-client-*` headers to ChatGPT `/transcribe`, where the request is rejected by the upstream WAF; its minimal allowlist does not forward `x-stainless-*` headers. Equivalent multipart requests made with curl succeed.

## What Changes

- Normalize non-native transcription request fingerprints to the established canonical Codex CLI persona before upstream forwarding.
- Keep native Codex transcription fingerprints unchanged.
- Preserve authorization, selected ChatGPT account id, multipart upload behavior, routing, timeout, failover, accounting, model-source forwarding, and OpenAI-compatible response shape.
- Add fake-upstream regression coverage for an OpenAI Node SDK-shaped `/v1/audio/transcriptions` request.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `audio-transcriptions-compat`: Require canonical Codex fingerprint normalization for non-native subscription-backed transcription forwarding.

## Impact

Change is limited to subscription-backed `/transcribe` outbound headers, transcription compatibility tests, and audio-transcriptions compatibility requirements. No setting, dependency, migration, external model-source behavior, or public response contract changes.
