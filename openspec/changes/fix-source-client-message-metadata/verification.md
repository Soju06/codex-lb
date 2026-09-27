# Verification

## Completeness and correctness

- A synthetic user message with the observed Codex metadata reproduced production HTTP 409 before this change.
- The metadata projection is shared by direct-source ownership extraction and portability. It never updates the forwarding body, and subscription replay keeps the existing validator.
- Fresh user/system/developer messages, ID-less messages, complete function/custom/apply-patch pairs, standalone outputs and inline agent messages are covered.
- Route coverage includes JSON/SSE, both Responses route families and trailing slashes; another application replica exercises retained call ownership. Unknown, conflicting, replaced, disabled and disallowed owners remain rejected.
- Malformed metadata, non-finite or boolean timestamps, invalid content-kind values and unknown metadata fields remain nonportable. Valid metadata cannot erase ordinary response, reasoning, compaction, assistant output or call state.
- Existing instruction normalization remains unchanged. Tests check original metadata on forwarded input items and the existing lifting of developer text into instructions.

## Checks completed

- New tests: 37 unit and 32 integration tests passed with SQLite (69 total).
- Mapped source/replay unit regressions, including the new unit tests: 624 passed.
- PostgreSQL: all 58 new/existing client-message routing tests passed, including source pooling and replica continuations.
- Clean checkout: full Ruff lint, format check (1,166 files), and type checking passed.
- Strict OpenSpec: change valid; 65 main specs passed.

The main workspace contains unrelated edits, including an unrelated type diagnostic in a chat-completions test. Verification and release isolation exclude those edits.

## Independent review

The independent Codex CLI review completed with no actionable correctness or security findings. The reviewer also ran the new tests and related regression coverage.

## Pending operational verification

The HA rollout and final archive are tracked in tasks.md. Production validation uses synthetic fresh messages, a function call/result and follow-up messages with the new metadata across all three backends and local/public endpoints. A genuinely old transcript with unknown upstream-owned reasoning remains outside this fix and still requires its original source.
