# Verification: fix-source-reference-scope-gaps

## Scope and outcome

This change fixes source continuity and candidate isolation across public and native Responses HTTP routes. Implementation was delegated to GPT-6-Sol with high reasoning; the primary agent reviewed the final code and ran independent validation. No commit, push, deployment, live upstream call, real credential change, or production database mutation was performed.

The initial fixes preserve original public-model ownership across normalized fallback, resolve MCP approval references, and validate object-form conversation overrides. The overall review additionally fixed effective input/hosted-tool file references, code-interpreter containers, file-search vector stores, supported fresh namespace/web-search declarations, empty stream options, JSON ownership-failure logging, and malformed candidate isolation.

## Requirement and scenario coverage

All 9 delta requirements and 23 scenarios match the main `model-source-routing/spec.md`. Stable operator context is synchronized in `model-source-routing/context.md`.

| Requirement | Implementation | Maintained evidence |
| --- | --- | --- |
| Original public-model scope | Both route calls and `_balanced_source_responses_response` in `api.py` | `test_source_reference_scope_gaps.py`: removed/disabled/streaming-ineligible original owner, valid fallback, same-row and client/model isolation; `test_source_reference_scope_replica.py`: second-app and trailing-slash routes |
| MCP approval ownership | `OwnershipScope.item_keys` reuses the item domain | Same/other/unknown approval route tests and second-app SSE continuations |
| Conversation override ownership | `OwnershipScope.request_keys` and candidate validation in `api.py` | Unknown, known, conflicting and malformed overrides; valid peer and single-source controls |
| Effective file references | Candidate and final-payload guards in `api.py` | `test_source_pool_overall_review.py`: real file pin, source override, hosted container files, original subscription route, unused bad candidate and malformed item/content/output types |
| Container ownership | Exact code-interpreter declaration/item extraction and durable recorder | Same/other/unknown tool and retained-input paths; second-app SSE publication and lookup |
| Vector-store ownership | Exact file-search declarations; existing successful request publication | Same/other/unknown vector-store route tests; independent history, key/model scope, credential revision and override probes |
| Declared fresh tools | Direct-source classification in `replay_safety.py` | Unchanged namespace and web-search JSON with one/two sources; existing portability tests preserve overflow restrictions |
| Empty stream options | Direct-source neutral-control classification | One/two-source route cases preserve `{}` |
| JSON publication failure | Explicit error finalization with captured upstream observations | JSON/SSE collision controls check upstream status, usage, timing, reservation release and no extra dispatch |

## Fail-before evidence

- Original defects: 4 maintained route cases failed before patching, then passed; expanded coverage reached 21 passing cases.
- Initial overall-review regressions: 16 failed and 10 positive controls passed before fixes (file overrides, containers, namespace and web-search declarations).
- Empty stream options and JSON publication observations: each had one failing case and one passing control before fixes.
- Hosted file references: 2 route failures; vector-store continuation: 1 reproduced failure.
- Cross-backend container continuation: 4 failures and 2 positive controls before fixes.
- Final malformed-input regression: 4 failures and 8 controls before the narrow correction; all 12 passed after. The public/native independent reproducers passed on the earlier snapshot and failed on the intermediate patch.

## Independent review

Three iterations used frozen checkouts and synthetic SQLite probes, with no production or PostgreSQL access by the reviewer:

1. Overall review covered custom-source CRUD/token, aliases/catalog/installer, transport/tool forwarding, pooling/retries, ownership publication/history, settlement/cancellation and HA contracts. Four actionable groups were fixed; related hosted-file and vector-store probes from the primary agent were also fixed.
2. Re-review passed 24 maintained cases and 3 additional probes. It independently reproduced one malformed-input candidate-isolation regression, which was fixed.
3. Final delta review reported **no actionable findings**, with **34 maintained route cases and 11 synthetic probes passed**, including both iteration-2 reproducers. App file hashes matched this frozen checkout after review.

Review logs: `/tmp/source-pool-sol-overall-review.log`, `/tmp/source-pool-sol-final-review.log`, `/tmp/source-pool-sol-third-review.log`.

## Validation completed

- Scoped Ruff check/format and ty: pass on all 3 edited app files and 3 new maintained test files.
- Proxy timing seams, cancellation safety, architecture and whitespace checks: pass on final code.
- Strict OpenSpec validation: active change valid; all 65 main specs pass. All 9 delta requirements are synchronized exactly.
- SQLite focused routes before the final narrow correction: 63 passed; final malformed-input correction: 12 passed. Earlier broader related source suite: 128 passed.
- Source-pool and portability unit suite after direct-source classification edits: 95 passed.
- Isolated PostgreSQL broad source/ownership suite: **225 passed in 15:17**. This process loaded the code before the final malformed-input correction; the final-code follow-up below covers that correction and all new regressions.
- Final-code isolated PostgreSQL route follow-up: **97 passed in 6:47**, covering all 3 new maintained files, both HTTP routes, trailing slashes, SSE/JSON ownership and fresh second-app state. Log: `/tmp/source-pool-sol-final-postgres-addendum.log`.
- Ownership migration tests: 2 passed on SQLite and 2 passed on an isolated PostgreSQL database, covering single-head graph, historical data preservation, downgrade/re-upgrade and schema drift.
- Earlier broad aliases/catalog/installer/routing/dispatch/transport run: 569 passed, 16 skipped, one timing-sensitive stall classification failure. The failure recorded 0.251 seconds waiting against a 0.3-second threshold; its focused repeat passed. No related implementation change followed.
- Frontend source/key flows: 47 passed with one create-key timing failure in the combined run; the focused API-key flow plus installer run passed all 9 cases.

## Limits and operational notes

PowerShell is unavailable, so 16 installer runtime cases remain skipped. Full-tree ty reports six preexisting diagnostics in unchanged tests; scoped checks pass. This is not a claim of a completely green full-repository suite or real-provider end-to-end validation.

Production has multiple active backends. Durable reference ownership is shared in PostgreSQL, while rotation, cooldowns and concurrency counters remain local to each worker. Upgrade every backend through the existing HA surge process before enabling multi-source operation; old binaries do not enforce the new reference checks. Old container references without recorded ownership fail closed in pools. See source-routing context for mixed-version and legacy-reference behavior.

The 147 preexisting workspace paths were hash-checked. Only the 3 scoped app files and main source-routing spec/context changed among those paths; unrelated pending work was preserved. No schema change was added for this fix.

## Final assessment

Completeness: 15/15 tasks, 9/9 requirements and 23/23 scenarios verified. Correctness: no unresolved actionable finding after the third independent review; final-code PostgreSQL follow-up passed. Coherence: existing scoped reference keys, durable publication, candidate-specific shaping, and dispatch finalization were reused; subscription-overflow classification and schema remained unchanged. The validation limitations above remain explicit. Ready to archive with specifications synchronized.
