## 1. Spec and Config

- [x] 1.1 Add `anthropic-messages-compat` capability delta for SDK-backed `/v1/messages` compatibility
- [x] 1.2 Add config/env surface for Anthropic mode and Linux-only credential discovery

## 2. Credentials and Upstream Clients

- [x] 2.1 Implement Linux credential discovery (`claude login` artifacts + helper command + env override)
- [x] 2.2 Implement Anthropic messages proxy client (stream + non-stream + upstream error passthrough)
- [x] 2.3 Implement Anthropic usage client for 5h/7d usage windows

## 3. Anthropic Module

- [x] 3.1 Add `/v1/messages` route with API key auth + model restriction enforcement
- [x] 3.2 Persist request logs and API key reservation settlement for Anthropic requests
- [x] 3.3 Add synthetic provider account handling for request-log/dashboard compatibility
- [x] 3.4 Ingest Anthropic usage windows into `usage_history` (`primary`=5h, `secondary`=7d)

## 4. Dashboard/Cost Wiring

- [x] 4.1 Add Claude model pricing aliases for cost computation
- [x] 4.2 Wire Anthropic usage refresh into scheduler path

## 5. Validation

- [x] 5.1 Add unit tests (credential discovery, usage parsing, streaming settlement parsing)
- [x] 5.2 Add integration tests (`/v1/messages` stream/non-stream/error + dashboard/request-log regressions)
- [ ] 5.3 Run `openspec validate --specs` and targeted pytest suites
