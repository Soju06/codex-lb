## 1. Shared multipart admission

- [x] 1.1 Implement immutable route policies, the counted request stream, bounded Starlette parser callbacks, exact byte/count failures, bounded file reads, and guaranteed `FormData` cleanup.
- [x] 1.2 Add path-family multipart limit/error handling plus a POST/path-scoped content-encoding marker that preserves auth precedence and `identity`, prevents decompression body reads, normalizes mounted paths, and leaves other methods/routes untouched.
- [x] 1.3 Add reusable typed-equivalent field extraction and explicit OpenAPI multipart request bodies without restoring FastAPI pre-dependency form parsing.

## 2. Authorized route integration

- [x] 2.1 Move account `auth_json` parsing after dashboard session/write authorization, require the bounded one-file shape, and close its spool before service, persistence, refresh, or audit work.
- [x] 2.2 Move native and `/v1` transcription parsing after proxy authorization, preserve native prompt and ordered source-model form fields, and close spools before reservation, selection, or upstream forwarding.
- [x] 2.3 Move `/v1/images/edits` parsing after proxy authorization, merge `image`/`image[]` in order, enforce image/mask/count/aggregate limits, preserve Pydantic validation, and record auth/encoding/parser rejections once without pre-auth form parsing.

## 3. Regression coverage

- [x] 3.1 Add shared unit coverage for declared, chunked, understated, exact, and crossing body/file/text/aggregate limits; file/field counts; spool rollover and cleanup; malformed input; disconnect; cancellation; `root_path`; auth-precedence content encoding; wrong methods/media types; and unrelated routes.
- [x] 3.2 Add account-import integration coverage for auth-before-read, exact and oversized uploads, invalid shapes, dashboard envelopes, cleanup before service work, and absence of persistence/audit side effects.
- [x] 3.3 Add both transcription-route regressions for byte-identical small/exact uploads, 25 MiB and 32 MiB failures, missing/extra parts, ordered source fields, no pre-rejection reservations/upstream calls, and OpenAI envelopes.
- [x] 3.4 Add image-edit regressions for combined `image`/`image[]` ordering and count, one mask, unknown files, per-file and aggregate boundaries, text/body limits, empty-part compatibility, no internal forwarding, cleanup, and one 413 observation.
- [x] 3.5 Add ratchets proving every FastAPI multipart route has an explicit policy, canonical OpenAPI request bodies remain present, and the non-multipart Codex image/files surfaces are unaffected.

## 4. Verification

- [x] 4.1 Run strict OpenSpec validation and all targeted multipart, account, transcription, image, middleware, SDK-compatibility, and observability suites.
- [x] 4.2 Run the complete unit and integration baselines in the repository's CI-equivalent shard configuration.
- [x] 4.3 Run Ruff check/format, ty, architecture and simplicity checks, OpenAPI diff review, and diff hygiene checks.
- [x] 4.4 Verify the implementation semantically against every OpenSpec scenario and review the final standalone diff against current upstream `main`.

## 5. Mainline ingress composition

- [x] 5.1 Merge the current raw-ingress baseline, reconcile middleware registration, and document the exact-route authorization-first carve-out while keeping every unrelated multipart request under generic admission.
- [x] 5.2 Add a production-stack regression proving middleware order, auth-first unencoded and encoded handling on all four dedicated operations, dedicated handling of `identity`, and generic guarding of unrelated multipart regardless of encoding.
- [x] 5.3 Run the combined multipart/raw/decompression suites and all repository readiness gates against the merged result.
