## ADDED Requirements

### Requirement: Native file upload registration endpoint

The system SHALL expose `POST /backend-api/files` as an authenticated proxy endpoint for the upstream Codex file upload protocol. The endpoint SHALL accept a JSON body containing `file_name` (non-empty string), `file_size` (positive integer, less than or equal to 512 MiB / `OPENAI_FILE_UPLOAD_LIMIT_BYTES`), and `use_case` (string, defaulting to `"codex"`). The service MUST forward the request to upstream `POST /files` using a selected account's Bearer token and `chatgpt-account-id`, and MUST return the upstream JSON body (including `file_id` and Azure Blob `upload_url`) verbatim. The service MUST NOT proxy or rewrite the SAS upload URL itself; clients PUT bytes directly to that URL.

#### Scenario: Native file create request is forwarded

- **WHEN** an authenticated client posts a valid `{file_name, file_size, use_case}` body to `/backend-api/files`
- **THEN** the service forwards the JSON body to upstream `POST /files` with the selected account's Bearer token and `chatgpt-account-id`
- **AND** returns the upstream response JSON verbatim with HTTP status 200

#### Scenario: File-size cap is enforced at the edge

- **WHEN** a client requests `file_size` greater than 512 MiB
- **THEN** the service returns HTTP 400 with an OpenAI-format `invalid_request_error` envelope
- **AND** does not call upstream

#### Scenario: Upstream registration error is propagated

- **WHEN** upstream `POST /files` returns a non-2xx response
- **THEN** the service returns the same HTTP status and an OpenAI-format error envelope derived from the upstream payload

#### Scenario: Use-case defaults to codex when omitted

- **WHEN** a client omits the `use_case` field
- **THEN** the service forwards `use_case = "codex"` to upstream

### Requirement: File finalize / status polling endpoint

The system SHALL expose `POST /backend-api/files/{file_id}/uploaded` as an authenticated proxy endpoint for upload finalization. The endpoint MUST accept a non-empty `file_id` path parameter and an empty JSON body. The service MUST forward the call to upstream `POST /files/{file_id}/uploaded` and MUST mirror the upstream Codex CLI's status-polling loop server-side: while the upstream returns `status == "retry"`, the service MUST re-poll every 250 ms up to a 30 s total budget, then return the most recent payload regardless of status. On `status` of `success` or `failed` the service MUST return immediately. The upstream JSON body (including `status`, `download_url`, `file_name`, `mime_type`, and `file_size_bytes`) MUST be returned verbatim.

#### Scenario: Finalize returns success on first poll

- **WHEN** upstream returns `status: success` on the first call
- **THEN** the service returns HTTP 200 with that payload and does not poll again

#### Scenario: Finalize loops on retry status until success

- **WHEN** upstream returns `status: retry` followed by `status: success`
- **THEN** the service polls every 250 ms and returns the eventual `success` payload verbatim

#### Scenario: Finalize budget bounds the retry loop

- **WHEN** upstream returns `status: retry` for the entire 30 s budget
- **THEN** the service returns HTTP 200 with the last `status: retry` payload so the caller can decide what to do
- **AND** does not block beyond the configured budget

#### Scenario: Finalize maps upstream error status

- **WHEN** upstream returns 404 for an unknown `file_id`
- **THEN** the service returns HTTP 404 with an OpenAI-format error envelope

### Requirement: File proxy routes share account-selection and request-log plumbing

File proxy routes MUST select an upstream account using the same load-balancer / freshness / 401-retry pattern as `/backend-api/transcribe`, and MUST persist a request-log entry on every attempt. Log entries MUST use synthetic model identifiers `files-create` (for `POST /backend-api/files`) and `files-finalize` (for `POST /backend-api/files/{file_id}/uploaded`) so dashboard request-log queries can filter file activity. Transport MUST be recorded as HTTP. File requests MUST NOT count against per-model API key limits unless the API key explicitly allows the synthetic model identifiers.

#### Scenario: 401 from upstream triggers forced refresh and one retry

- **WHEN** upstream `POST /files` returns 401 on the first attempt
- **THEN** the service forces a token refresh and retries once with the refreshed account metadata

#### Scenario: Request-log entry is written for both success and failure

- **WHEN** any `/backend-api/files` or `/backend-api/files/{id}/uploaded` request completes (success or error)
- **THEN** a request-log row is persisted with model `files-create` or `files-finalize`, transport `http`, and the appropriate status / error code

### Requirement: File create and finalize routes require API key authentication when enforcement is enabled

The system MUST apply the same `validate_proxy_api_key` and dashboard `apiKeyAuthEnabled` gating to the file routes as it does to `/backend-api/transcribe`. When API key auth is enabled and a request lacks a valid API key, the service MUST return HTTP 401 with an OpenAI-format `invalid_api_key` error.

#### Scenario: File create is rejected without API key when auth enforcement is on

- **WHEN** dashboard `apiKeyAuthEnabled` is true and a client posts to `/backend-api/files` without a valid API key
- **THEN** the service returns HTTP 401 with `error.code = "invalid_api_key"`

#### Scenario: File finalize is rejected without API key when auth enforcement is on

- **WHEN** dashboard `apiKeyAuthEnabled` is true and a client posts to `/backend-api/files/{file_id}/uploaded` without a valid API key
- **THEN** the service returns HTTP 401 with `error.code = "invalid_api_key"`
