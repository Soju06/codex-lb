## Why

The streaming Responses latency hardening now bounds `/v1/responses` request time under upstream instability, but the same protection does not yet apply to compact and transcription proxy flows.

`ProxyService.compact_responses()` and `ProxyService.transcribe_audio()` still perform account selection and freshness checks before calling upstream without a shared request deadline, so selection, token refresh, and connect time can accumulate unpredictably on those routes (`app/modules/proxy/service.py:117-230`, `app/modules/proxy/service.py:270-360`).

There is also a contract difference between the two routes:
- compact intentionally defaults to no upstream read timeout to match Codex CLI behavior (`openspec/specs/responses-api-compat/spec.md:257-266`, `app/core/clients/proxy.py:987-992`)
- transcription already uses a fixed upstream timeout budget in code, but that budget is hard-coded and not coordinated with account refresh/retry behavior (`app/core/clients/proxy.py:1096-1100`)

## What Changes

- extend bounded-latency rules to compact and transcription request paths
- define how compact can bound selection / refresh / connect latency without regressing its default no-read-timeout contract
- replace transcription's hard-coded timeout behavior with configurable request-budget semantics aligned with the streaming route
- add regression coverage for compact/transcription budget exhaustion and retry handling

## Impact

- Code (follow-up implementation target): `app/modules/proxy/service.py`, `app/core/clients/proxy.py`, `app/core/auth/refresh.py`, `app/core/config/settings.py`
- Tests (follow-up implementation target): compact/transcription proxy unit and integration coverage
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/audio-transcriptions-compat/spec.md`
