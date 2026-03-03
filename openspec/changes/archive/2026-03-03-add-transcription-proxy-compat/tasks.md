## 1. OpenSpec Contract Updates

- [x] 1.1 Add proposal/design/spec deltas for transcription endpoints and API key scope updates
- [x] 1.2 Validate OpenSpec artifacts for the new change

## 2. Core Transcription Proxy Implementation

- [x] 2.1 Add multipart transcription upstream client helper in `app/core/clients/proxy.py`
- [x] 2.2 Add transcription service flow in `app/modules/proxy/service.py` with account selection, refresh retry, and upstream error handling
- [x] 2.3 Add `POST /backend-api/transcribe` and `POST /v1/audio/transcriptions` routes in `app/modules/proxy/api.py`
- [x] 2.4 Wire any new router registrations in `app/main.py`

## 3. Policy Enforcement for Transcription Routes

- [x] 3.1 Enforce OpenAI compatibility model validation (`gpt-4o-transcribe`) on `/v1/audio/transcriptions`
- [x] 3.2 Reuse API key model access checks and model-scoped request limit enforcement with effective model `gpt-4o-transcribe`
- [x] 3.3 Ensure reservation lifecycle cleanup on success and failure paths

## 4. Tests and Verification

- [x] 4.1 Add integration tests for transcription route happy paths and OpenAI model validation errors
- [x] 4.2 Add integration tests for auth/model restriction behavior on transcription endpoints
- [x] 4.3 Add regression test for model-registry missing transcription model while routing still succeeds
- [x] 4.4 Run targeted test suites and record results

## 5. Post-Review Regression Fixes

- [x] 5.1 Strip inbound `Content-Type` case-insensitively before multipart transcription forwarding
- [x] 5.2 Recompute `chatgpt-account-id` from refreshed account metadata on 401 transcription retry
- [x] 5.3 Add regression tests for header stripping and refreshed account-id retry behavior

## 6. Timeout Error Mapping Fixes

- [x] 6.1 Catch transcription upstream request timeouts and wrap them as `ProxyResponseError` with code `upstream_unavailable`
- [x] 6.2 Add unit regression coverage for timeout-to-error-envelope mapping in transcription proxy client

## 7. Review Quality Gate Fixes

- [x] 7.1 Narrow captured transcription headers in unit regression test to satisfy static typing checks
- [x] 7.2 Reformat transcription integration regression block to satisfy Ruff formatter checks

## 8. Reliability Regression Fixes

- [x] 8.1 Map transcription response-body read timeouts/transport failures to `upstream_unavailable` before generic JSON-parse fallback
- [x] 8.2 Catch initial transcription `_ensure_fresh` refresh failures and return handled proxy errors instead of unhandled 500s
- [x] 8.3 Add regression coverage for response-body read timeout mapping and initial refresh-failure handling
