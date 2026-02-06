## Context

codex-lb proxies OpenAI-compatible APIs to the ChatGPT backend. The backend rejects audio file types, so chat `input_audio` fails downstream. Meanwhile codex-lb locally rejects `input_file.file_id`, but the backend accepts file_id semantics (returns 404 when the id is unknown). We need to align codex-lb behavior with upstream support and errors.

## Goals / Non-Goals

**Goals:**
- Reject chat `input_audio` early with invalid_request_error.
- Allow `input_file.file_id` in Responses/Chat to pass through to upstream.
- Update compatibility documentation and live checks to reflect upstream behavior.

**Non-Goals:**
- Implement file upload endpoints or file_id creation.
- Add support for audio file types.

## Decisions

1. **Reject input_audio at validation**
   - Treat `input_audio` as unsupported in chat user content validation.
   - Rationale: avoid inconsistent downstream errors and match upstream rejection.

2. **Allow file_id pass-through**
   - Remove local validation that rejects `input_file.file_id`.
   - Rationale: upstream supports file_id semantics; local rejection diverges from backend behavior.

3. **Document upstream behavior explicitly**
   - Update support matrix and live check expectations to show audio unsupported and file_id requires upload.

## Risks / Trade-offs

- **[Risk] file_id requests still fail without uploads** → Mitigation: document that file_id requires a valid upload; keep error messaging from upstream.
- **[Risk] Early audio rejection hides upstream error detail** → Mitigation: provide clear invalid_request_error and document audio unsupported.

## Migration Plan

1. Update request validation for input_audio and file_id.
2. Update tests to reflect new behavior.
3. Update compatibility docs and live check expectations.

## Open Questions

- Should audio rejection return a more specific error message to mirror upstream text?
