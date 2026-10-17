- [x] 1. Reproduce the production turn-state-only startup cooldown as a unit
      regression that currently returns 503 before submission.
- [x] 2. Hold only explicitly enabled, zero-event, durable operation-fenced hard
      turns through the bounded cooldown.
- [x] 3. Preserve fail-closed behavior when the durable session/owner proof is
      absent and keep one-shot recovery bounded by the existing atomic claim.
- [x] 4. Run focused tests, relevant bridge suites, Ruff, type/architecture
      checks, whitespace checks, and strict OpenSpec validation.
