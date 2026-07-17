## 1. Raw peer boundary

- [x] 1.1 Add a typed raw-socket-peer scope helper and an outer ASGI middleware that captures HTTP/WebSocket peers before delegating to Uvicorn's proxy-header middleware with the unchanged `FORWARDED_ALLOW_IPS` contract
- [x] 1.2 Register the capture/projection middleware as the outermost application middleware without changing other middleware or generic `request.client` consumers
- [x] 1.3 Make `proxy_unauthenticated_client_cidrs` use only the preserved raw peer and fail closed when it is unavailable

## 2. Owned launch paths

- [x] 2.1 Disable server-level proxy-header projection in `app.cli` and the direct Docker Compose Uvicorn command
- [x] 2.2 Add the no-proxy-headers option to every shipped direct FastAPI/Uvicorn command for the main application while leaving `app.cli`-based entrypoints unchanged

## 3. Regression coverage

- [x] 3.1 Cover HTTP and WebSocket capture/projection, middleware ordering, trusted and untrusted peers, and the unset/empty/wildcard/explicit `FORWARDED_ALLOW_IPS` cases
- [x] 3.2 Cover HTTP and WebSocket authentication where a forwarded allowlisted identity differs from the non-allowlisted raw peer, plus the valid raw-allowlisted path
- [x] 3.3 Lock the CLI, Compose, documentation, Docker, distroless, production Compose, and Helm launcher contracts

## 4. OpenSpec synchronization

- [x] 4.1 Sync the verified launcher requirement and stable operational context into `deployment-installation`, and confirm the implementation satisfies the existing `api-keys` raw-peer contract without duplicating it

## 5. Verification

- [x] 5.1 Run focused tests, Ruff format/check, full `ty check`, strict change and main-spec validation, and `git diff --check`
- [x] 5.2 Run the repository fast CI gate and confirm its top-level exit result
- [x] 5.3 Run the adversarial Codex review loop against current `main` and address all in-scope findings
