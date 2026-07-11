# Design

## Compatibility boundary

This change treats the body-derived `additional_tools` shape implemented by #1161 as canonical. It does not re-trust or preserve an inbound Lite header and does not weaken previous-response linkage checks. The added guards operate after the request has been classified.

## Compact wire budget

Compact trimming preserves `additional_tools`, typed or role-only system/developer state, required state-tool calls, matching outputs, and the latest input item. Tool-call/output matching is occurrence-aware when a call ID is reused, so a required state call does not retain historical outputs from an earlier call with the same ID. When the latest item has a matching tool-call pair in the supplied input, the pair is part of the required selection; the request fails closed if the pair cannot fit. Exact-budget backtracking removes an optional tool pair as one group instead of re-adding its counterpart from the approximate item budget. Token estimation uses the complete JSON array representation so brackets, separators, whitespace, and escaped non-ASCII content count toward the cap. After image URLs are inlined, the final transformed input is checked again before logging or starting an upstream request.

An untrimmable request raises the standard `responses_compact_input_too_large` client error. Terminal compaction triggers perform the same validation before admission and reservation work. A rejection discovered after API-key reservation releases that reservation and does not penalize an upstream account.

## Policy and replay

API-key model enforcement evaluates the original Lite-shaped input after canonical model alias normalization. A catalog-confirmed target with `use_responses_lite = false` is rejected before forwarding.

Code-mode `exec` and `collaboration` calls are downstream side effects. Reconnect replay suppresses the same call identity, while request-history deduplication keeps different call IDs and their matching outputs even when their source text is identical.
