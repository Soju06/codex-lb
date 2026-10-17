## 1. Contract

- [x] 1.1 Record hop-local routing-header and backend non-streaming parity
  requirements.

## 2. Implementation

- [x] 2.1 Remove `x-codex-routing-hint` from every upstream HTTP and WebSocket
  header builder without changing local routing inputs.
- [x] 2.2 Preserve backend `stream: false` through upstream HTTP and translate
  the single upstream JSON response through the existing accounting flow.

## 3. Verification

- [x] 3.1 Add focused unit and route-level regressions for header omission,
  HTTP-only transport, upstream request shape, and downstream JSON shape.
- [x] 3.2 Run focused tests, lint/type checks, strict OpenSpec validation, and
  a controlled-origin parity probe.
