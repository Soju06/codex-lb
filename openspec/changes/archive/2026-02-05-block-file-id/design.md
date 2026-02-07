## Context

codex-lb proxies OpenAI-compatible APIs to the ChatGPT backend while rotating upstream accounts. The system does not provide a file upload endpoint, so `file_id` references are not resolvable and can route to the wrong account. Allowing `file_id` creates inconsistent behavior and downstream 404s. We need to block `file_id` inputs early and return an OpenAI-style invalid_request_error with upstream-matching wording.

## Goals / Non-Goals

**Goals:**
- Reject `input_file.file_id` in `/v1/responses` with a consistent invalid_request_error.
- Reject chat `file` content parts that include `file_id` in `/v1/chat/completions`.
- Align local error envelopes for these rejections to upstream wording (`Invalid request payload`) and stable param placement.

**Non-Goals:**
- Implement file upload endpoints or file_id creation.
- Change support for `file_url` or `file_data` inputs.
- Alter upstream error handling for non-validation failures.

## Decisions

1. **Block file_id at request validation**
   - Add a Responses input validator that scans input items and rejects any `input_file` with `file_id`.
   - Add a Chat Completions user content validator that rejects `file` parts containing `file_id`.
   - Rationale: fail fast with a stable 4xx error and avoid account-rotation ambiguity.

2. **Use upstream-matching error envelope for rejections**
   - Keep `type=invalid_request_error`, `code=invalid_request_error`, and `message="Invalid request payload"`.
   - Set a stable `param` to avoid deep index paths (e.g., `input` for Responses, `messages` for Chat) by raising validation errors at the top-level field validators.
   - Rationale: upstream returns consistent wording; local errors should match as closely as possible.

3. **Document the breaking change**
   - Update compatibility specs and test plan to mark file_id unsupported.
   - Rationale: avoids confusion for clients expecting OpenAI-style file_id reuse.

## Risks / Trade-offs

- **[Risk] Breaking change for clients using file_id** → Mitigation: document rejection and recommend `file_url` or `file_data` inputs.
- **[Risk] Param mismatch with upstream expectations** → Mitigation: keep param at top-level field names for stability and align with common OpenAI invalid_request_error patterns.

## Migration Plan

1. Update Responses and Chat validation to reject `file_id`.
2. Update tests to expect 400 invalid_request_error with `Invalid request payload`.
3. Update compatibility docs and live check expectations.

## Open Questions

- None.
