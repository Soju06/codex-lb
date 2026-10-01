# Verification

The new parser-to-monetary regression failed both ordinary and Ultrafast
integral cases at 19 instead of 20 microdollars before the production change.
Both genuine-fraction cases already passed. After decimal source scaling:

- 509 tests passed across pricing, catalog, metadata, API keys, request logs,
  usage updates and pricing-source integration.
- Changed-file Ruff check/format, application ty and wheel build passed.
- Both changed Python files have clean language-server diagnostics.
- Strict OpenSpec change validation passed; canonical requirements are synced.
- Fourteen real Responses HTTP cases passed, including installed LiteLLM
  catalog requests settling 100 input tokens at 20 microdollars in both modes.
  Stored/displayed cost and reservation/limit values agree. Further requests
  return 429 without upstream dispatch; synthetic-key deletion returns 204.
- App/upstream servers were awaited and temporary databases/keys removed.

Full local CI remains limited by the previously documented missing cargo-deny.
The current-head GitHub Docker job builds the image successfully but its Trivy
gate flags two existing HIGH urllib3 2.7.0 advisories. The lockfile is identical
to the upstream baseline; no dependency/Docker/workflow file is changed here.
This is not reported as green or as a pricing regression.

Self-review confirms the change is confined to validated source-unit scaling,
shares existing settlement/rate selection, preserves genuine fractional
truncation and adds no dependency or historical repricing.
