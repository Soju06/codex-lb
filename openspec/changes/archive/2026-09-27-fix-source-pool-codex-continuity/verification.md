# Verification: Codex continuity after source-pool expansion

Verified locally on 2026-09-27. At initial verification, production remained on `eb2df654`; the change had not been committed or deployed, and the four additional production sources remained disabled. Final pre-shipping review evidence is recorded in `review.md`.

## Reproduction and coverage

Two public-route regressions reproduced HTTP 409 before the code change. The new route suite covers both canonical Responses endpoints and trailing slashes, expansion from one to five sources, unchanged forwarded message IDs and search declarations, durable response/encrypted-state ownership on another application instance, disabled owners, unknown assistant IDs, item references, encrypted state, and malformed client messages.

Unit coverage checks user/system/developer messages with and without an explicit message type; malformed, assistant, file-bearing and opaque messages; valid and invalid web-search content types; unchanged response-ID extraction; and unchanged subscription replay classification.

The exact reported Desktop request was not archived. A synthetic request captured from installed Codex CLI 0.157.1 using the source model catalog reproduced the two compatibility blockers. Replaying that captured body through `/backend-api/codex/responses` with one and then five sources passed. This is independent reproduction, not a claim that the original Desktop body was recovered.

## Validation results

| Check | Result |
| --- | --- |
| Focused portability/source-pool unit suites, including the 29 new cases | 127 passed |
| SQLite route, client compatibility, replica ownership and prompt regressions | 117 passed |
| PostgreSQL route, client compatibility and replica ownership regressions | 56 passed |
| Existing source safety and first/second ownership review regressions | 69 passed |
| Captured CLI request through the native public route | 1 passed |
| Scoped Ruff lint and formatting; scoped `ty check` | Passed |
| Architecture, cancellation and timing guards | Passed |
| Strict OpenSpec change and main-spec validation | Passed; 65 main specs |
| `git diff --check` | Passed |

The initial post-fix route run exposed four test expectation errors: string input is normalized to message objects by the existing request parser. The fixture now uses explicit message objects, and the final SQLite and PostgreSQL runs above pass. No production parser change was needed for that correction.

The temporary PostgreSQL test container was stopped after verification. The workspace contains unrelated changes; full-repository CI and frontend checks were not rerun for this two-module Python correction.

## Requirement and design alignment

Both added requirements and all six scenarios are synced to `openspec/specs/model-source-routing/spec.md`. Stable rationale and an ownership example are in the capability's `context.md`. Classification works on copies; source wire bodies and subscription replay remain unchanged. Existing unknown, conflicting, disabled and revised ownership guards remain authoritative. No database migration, runtime setting or asynchronous lifecycle change is introduced.

Production re-enablement is outside this local verification. Roll out through the existing HA surge workflow before using the corrected selector on all backends.

## Follow-up review

The pre-shipping review found that checking a client role before invoking the general account-neutral validator also admitted an ID-bearing `additional_tools` bundle with `role: developer`. This incorrectly removed a non-message item ID from source ownership checks. One unit regression and both trailing-slash public Responses routes reproduced the gap: the routes returned 200 where 409 was required. All three regression cases failed before the correction.

The allowance now first requires the item's type to be absent or exactly `message`. Bundle IDs remain ownership evidence. This is a narrow correction to the local patch; the affected patch had not reached production.

The updated unit and public-route suites pass together on SQLite: 56 cases (30 unit, 26 integration). All 26 public-route cases also pass on PostgreSQL. The captured synthetic Codex CLI body still passes the one-to-five-source transition through the native public route. Scoped lint, formatting and typing checks pass, and strict validation passes for the change and all 65 main specs.
