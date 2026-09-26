## Context

See proposal.md. The routes retain the pre-selection model only temporarily; after selection the payload model becomes the matched source model. Ownership keys are scoped to API-key identity plus public model. Current request extraction ignores object-form conversations and MCP approval references.

## Goals / Non-Goals

**Goals:** Keep known original-scope ownership authoritative during fallback selection; cover all relevant reference shapes through the same ownership resolver and public routes.

**Non-Goals:** New storage, changed source scoring, or changes to subscription/file routing.

## Decisions

- Carry the original effective public model into the source-pool resolver. Before allowing a different normalized model, query the original scope for known ownership of the original request references. Any original-scope ownership vetoes this fallback: the original model is no longer selectable, even if the source row is the same, and the transport revision for that original model cannot be assumed from the fallback mapping. Do not require original-scope evidence when only fallback-scope evidence exists, because that would reject a valid normalized-fallback continuation. Alternative of denying all referenced fallbacks would break that case.
- Reuse the `item` ownership domain for `approval_request_id`, matching publication of `mcp_approval_request.id`. A separate approval domain would require duplicate publication and would leave older records unresolved.
- Extract `conversation.id` in requests the same way it is extracted in responses. The pool already distinguishes original client references from override-introduced references, so the existing single-source compatibility rule remains intact. Treat malformed override structures according to the existing request validation contract, without silently inventing ownership from them.
- Add maintained route tests with synthetic upstream responses and SQLite fixtures. First run the new cases against the pre-fix code to retain fail-before evidence.
- After applying each source's request overrides, extract `input_file` and `input_image` file IDs using the existing request extractor. A source candidate with a file ID is ineligible; if no safe candidate remains, return 409 before source admission or reservation. Do not reroute the overridden request to a subscription account, because account selection already used the original client body. An original client file reference still takes the established subscription path.
- Validate the `type` slot of effective input items and their content/output parts before file-reference extraction. The extractor expects string item types. Filter a malformed candidate without letting its parse error convert another candidate's safe request into a 502 lookup failure.
- Publish `code_interpreter_call.container_id` in a new container ownership domain and resolve a string `tools[].container` against it. Keep `{"type":"auto"}` as a fresh declaration, and leave single-source external-state compatibility intact when ownership is otherwise unknown. Extract container references from retained code-interpreter input items as well as tool declarations.
- Use the same exact-shape approach for `file_search.vector_store_ids`: publish IDs from successful single-source request declarations via existing request-reference publication, then resolve IDs in later declarations before source selection. Avoid scanning arbitrary function schemas or assuming response items contain those IDs. Keep original single-source external state compatible, while the existing override-introduced reference rule rejects unknown IDs.
- Widen only the direct-source classification view for declared `namespace` tools with nonblank names and `web_search.external_web_access` booleans. The account-neutral replay predicate for subscription overflow remains unchanged. Reject unexpected declaration fields and reference-bearing nested state rather than treating all hosted tools as portable.
- Classify an empty `stream_options` object as a neutral direct-source control; do not alter subscription-overflow replay policy.
- On JSON ownership-publication failure, explicitly finish the dispatch with the already observed upstream status, usage and timings before rethrowing the ownership error. `SourceDispatch.finish(status="error")` releases the reservation and writes diagnostic data. The outer forwarding-error catch is idempotent, so it cannot overwrite the completed log; the client still receives the ownership error and no retry occurs.

## Risks / Trade-offs

- A fallback request may encounter two scoped ownership histories. Conflicting evidence must reject before upstream dispatch, even if the selected scope alone would accept the request.
- Additional original-scope lookup adds a database read only when normalization changes the model and request references exist. Batch reference lookup with existing ownership queries where practical.
- A configured override can make one source unusable. Filter that candidate without denying a different source whose effective payload is safe; keep the final request's reference validation source-specific.
- A failed publication is an error even when upstream returned 200. Retain the observed upstream data in its request log without charging the client for withheld output.
