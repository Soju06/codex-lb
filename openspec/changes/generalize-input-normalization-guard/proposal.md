# Generalize Input Normalization Guard

## Summary

Only lift input items that are actual instruction messages into `instructions`; pass every other typed `system`/`developer` input item through untouched, regardless of request shape.

## Motivation

#1161 fixed #1157 by returning early from `_normalize_responses_input_instructions` when the input carries a Responses-Lite `additional_tools` bundle. The underlying hazard is broader (#1171): the hoisting loop folds **any** `system`/`developer`-role input item into `instructions`, so a future non-message item type with a developer role and no `content` — in a request without the Lite prefix — would still be silently dropped, reproducing the same class of bug.

## Scope

- Guard the hoisting loop: only items whose `type` is absent or `"message"` are lifted; any other typed item keeps its position and wire shape.
- Keep the merged #1161 behavior (Lite early return, `to_payload()` round-trip) intact.
- Regression coverage with a synthetic non-message developer item type for `ResponsesRequest` and `ResponsesCompactRequest`.

Approach from #1159 / #1158, as requested in #1171.

## Out of Scope

- Modeling specific non-message item shapes; they are forwarded opaquely to stay codex-faithful.
- Changing how message-shaped instruction items are hoisted or merged.
