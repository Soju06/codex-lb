# Verification

The projection uses the existing registry field and preserves only strings.
The unit cases cover exact whitespace and Unicode, empty strings, missing
values, null, booleans, numbers, arrays and objects. The route cases create a
source through the API and verify both Codex catalog endpoints, including
unchanged client capability metadata.

Before the fix, the focused regression selection had 4 failures and 16 passes:
the two nonempty string unit cases and both valid-string route cases failed
because the emitted instructions were empty. After the fix, all 107 tests in
`tests/unit/test_model_sources_catalog.py` and
`tests/integration/test_v1_models.py` passed.

`make lint`, `uv run ty check`, strict change validation and strict validation
of all 67 main specifications passed. Independent source and contract review
found no actionable issues. The delta is synchronized to the main catalog
specification and its context explains import and pinned-catalog refresh.

This verification covers catalog projection. It does not claim a real
parent/child session or change the namespace-tool contract. The full local
`make ci` gate was not run.
