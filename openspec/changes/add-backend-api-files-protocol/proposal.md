# Change Proposal

Codex CLI uploads large prompt attachments (e.g. images, PDFs) through a 3-step file upload protocol that bypasses the upstream `/responses` 16 MiB websocket message ceiling. Today codex-lb has no `/backend-api/files` routes and rejects any prompt that contains an `input_file.file_id` reference, so users with long conversations and many attachments hit the 16 MiB cap (issue #510). They have no supported way to upload a file once and then reference it by `file_id` in subsequent turns.

## Changes

- Add `POST /backend-api/files` and `POST /backend-api/files/{file_id}/uploaded` proxy routes that mirror the upstream ChatGPT contract. Step 2 (`PUT {sas_upload_url}`) intentionally does not flow through codex-lb -- clients PUT bytes directly to the Azure Blob SAS URL.
- Add an upstream client module (`app/core/clients/files.py`) that posts to the upstream `/files` and `/files/{id}/uploaded` endpoints with Bearer + `chatgpt-account-id` auth, mirrors the upstream Codex CLI's 30 s retry-with-250-ms-delay loop on finalize, and maps non-2xx responses / transport failures to a `FileProxyError`.
- Reuse the existing account-selection / freshness / 401-retry / request-log plumbing on `ProxyService` so file routes are subject to the same load balancing, rate limit accounting, and audit logging as `/responses` and `/transcribe`. File requests are logged with synthetic models `files-create` and `files-finalize` so they are queryable from the dashboard request logs.
- Stop rejecting `input_file` content items with `file_id` in `ResponsesRequest._validate_input_type` and `ResponsesCompactRequest._validate_input_type`. These items are now forwarded verbatim so they reference uploads registered through the new protocol. The `_has_input_file_id` / `_is_input_file_with_id` helpers stay for diagnostics.
- Enforce the upstream `OPENAI_FILE_UPLOAD_LIMIT_BYTES = 512 MiB` ceiling at the proxy edge via the new `FileCreateRequest` Pydantic schema so a misbehaving client cannot allocate an oversized SAS upload URL on a shared account.
- Persist finalize `download_url`, `mime_type`, and `file_name` in the in-memory file-pin entries so `input_image.file_id` and `input_image.image_url = "sediment://file_*"` can be resolved back to the uploaded bytes.
- Inline-rewrite uploaded `input_image` references to codex-faithful `data:` URLs before `/responses` forwarding, using the same 2048x2048 image normalization contract as codex and rejecting processed payloads above 16 MiB.
- Fail fast when the upstream HTTP responses bridge closes cleanly (`close_code = 1000`) before emitting any `response.*` event so rejected payloads do not spin through the full request budget.
