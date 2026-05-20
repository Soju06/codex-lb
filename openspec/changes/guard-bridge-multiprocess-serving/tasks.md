## 1. Runtime Guard

- [x] 1.1 Reject `uvicorn` worker counts above one when the HTTP responses session bridge is enabled.
- [x] 1.2 Preserve multi-worker serving when the HTTP responses session bridge is disabled.
- [x] 1.3 Add focused CLI coverage for both paths.

## 2. Docker Deployment Shape

- [x] 2.1 Update direct Docker documentation to use the image startup script and bridge-safe workers.
- [x] 2.2 Update the local Docker helper to rebuild the current checkout, recreate `codex-lb-direct`, and avoid raw git mutation.
- [x] 2.3 Align `.env.example` with the safe default worker count.

## 3. Verification

- [x] 3.1 Run focused unit tests.
- [x] 3.2 Rebuild/restart the live direct container with the safe startup path.
