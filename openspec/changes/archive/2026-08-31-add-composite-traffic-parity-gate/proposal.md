# Change: Add a composite traffic parity gate

## Why

The repository can already compare same-run HTTP/SSE/WebSocket semantics,
repeated TLS ClientHello cohorts, and controlled raw HTTP/2 profiles. Those
checks currently produce separate reports, so an operator can accidentally
treat partial evidence or a missing transport as a complete parity result.
Raw HTTP/2 comparison also records DATA frame lengths but does not yet compare
the body-size-independent segmentation policy.

## What Changes

- Add one privacy-safe composite CLI that consumes the three existing evidence
  families and emits a compact JSON/Markdown verdict.
- Fail closed unless same-run semantic parity, required HTTP/SSE/WebSocket
  coverage, repeated TLS parity for every required transport, direct HTTP/2
  repeatability, and routed HTTP/2 parity all pass.
- Add a stable HTTP/2 DATA segmentation dimension that distinguishes maximum
  sized frames, partial tail frames, and END_STREAM behavior without comparing
  request bodies or their exact lengths.
- Include evidence file digests and informational latency distributions without
  turning timing from separate invocations into an equality gate.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic-analysis CLIs, raw HTTP/2 comparator, tests, and
  operator documentation
- Runtime proxy behavior and public APIs are unchanged.
