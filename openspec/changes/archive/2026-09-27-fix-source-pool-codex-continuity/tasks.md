## 1. Reproduce and specify

- [x] 1.1 Inspect the production request failure and capture a synthetic Codex client request without changing production source state.
- [x] 1.2 Specify client-message and search-control classification boundaries.
- [x] 1.3 Add public-route regressions that fail under the existing multi-source selector.

## 2. Implement

- [x] 2.1 Share strict self-contained client-message classification between source reference extraction and direct-source portability.
- [x] 2.2 Admit validated web-search content types only for direct-source classification, preserving wire payloads.
- [x] 2.3 Verify known/unknown/conflicting ownership and subscription replay policies remain intact.

## 3. Validate and document

- [x] 3.1 Run route and replica regressions on SQLite and PostgreSQL, focused unit suites, lint/type/architecture checks, and the captured-client probe.
- [x] 3.2 Sync normative requirements and stable context, validate OpenSpec strictly, record evidence and archive the verified change.

## 4. Follow-up review before shipping

- [x] 4.1 Reproduce the client-message classifier incorrectly accepting an ID-bearing `additional_tools` bundle at a public Responses route.
- [x] 4.2 Restrict the allowance to typed or untyped message items; retain other item IDs and existing ownership checks.
- [x] 4.3 Verify the added boundary and prior client-message cases, update the verification evidence and archive again.
