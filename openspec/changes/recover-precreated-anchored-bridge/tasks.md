- [x] 1. Add the narrow same-anchor pre-created recovery predicate and pass its
      opt-in only from the missing-`response.created` watchdog.
- [x] 2. Keep account, durable-operation, admission, and reservation ownership
      unchanged across the recovery; retire and settle on failure.
- [x] 3. Add route-level regression coverage for silent upstream recovery and
      exhaustion, plus negative unsafe-continuation coverage.
- [x] 4. Run targeted pytest, Ruff, type/architecture checks, whitespace checks,
      and strict OpenSpec validation via
      `npx --yes @fission-ai/openspec@1.9.0`.
