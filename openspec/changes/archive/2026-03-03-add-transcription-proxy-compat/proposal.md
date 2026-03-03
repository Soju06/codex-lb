## Why

Codex voice input sends audio to `/backend-api/transcribe`, and OpenAI-compatible clients send audio to `/v1/audio/transcriptions`. `codex-lb` currently does not handle either route, so transcription requests cannot be routed through the load balancer.

## What Changes

- Add a native transcription proxy route at `POST /backend-api/transcribe` that accepts multipart audio uploads and forwards them upstream to `/transcribe`.
- Add an OpenAI-compatible transcription route at `POST /v1/audio/transcriptions`.
- Enforce a strict compatibility model contract on the OpenAI route: `model` MUST be `gpt-4o-transcribe`; otherwise return an OpenAI-format invalid request error.
- Pass through optional `prompt` to upstream transcription requests without transformation.
- Reuse existing proxy authentication, model restriction, and rate-limit enforcement with effective model `gpt-4o-transcribe`.
- Keep transcription account selection resilient by avoiding model-registry plan filtering for this fixed-model route.
- Add integration tests covering routing, validation, auth scope, model restrictions, and error propagation.

## Capabilities

### New Capabilities

- `audio-transcriptions-compat`: Transcription proxy support for native Codex and OpenAI-compatible transcription endpoints.

### Modified Capabilities

- `api-keys`: Extend proxy guard/model restriction requirements to cover transcription routes and fixed-model enforcement behavior.

## Impact

- **Code**:
  - `app/modules/proxy/api.py`
  - `app/modules/proxy/service.py`
  - `app/core/clients/proxy.py`
  - `app/main.py`
- **Tests**:
  - `tests/integration/test_proxy_transcriptions.py` (new)
  - `tests/integration/test_api_keys_api.py` (transcription model restriction coverage)
- **APIs**:
  - New `POST /backend-api/transcribe`
  - New `POST /v1/audio/transcriptions`
