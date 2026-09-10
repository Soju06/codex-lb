# Verification

- Current-main baseline: the six deterministic Responses burst probes produced
  four failures (direct/routed JSON and HTTP errors) and two SSE passes.
- After the fix: 112 native adapter, routed transport, and SSE/Compact integration
  tests passed with a real worker built from the locked workspace. This includes
  all six public Responses burst probes and stalled-consumer isolation.
- Ruff, changed-file ty, proxy architecture, cancellation safety, timing seam,
  and strict OpenSpec change validation passed.
- The only runtime change yields between accepted native events. Queue bounds,
  helper generations, cancellation, routing, and replay eligibility are unchanged.
- No outstanding implementation or scenario gaps were found.
