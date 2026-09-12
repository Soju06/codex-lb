# Change: Extend native SSE framing to routed Responses HTTP

## Why

Direct native Responses streams already frame SSE in Rust. Account-routed
streams still omit those options and wrap the native response as raw content,
so Python continues to decode and scan the bytes. The existing worker contract
can handle both paths without another protocol or framing implementation.

## What Changes

- Add an explicit native SSE option to unbuffered `CodexClient` requests and
  pass it to each selected native endpoint attempt.
- Preserve the native response type through routed Responses consumption so
  the existing framed-event iterator owns body reads and idle deadlines.
- Keep Python endpoint selection, fallback eligibility, trace metadata,
  normalization, error mapping, archives, and cleanup ownership.
- Verify both direct and routed paths against the same real-worker cases,
  including unavailable-helper fallback and endpoint retry boundaries.

## Impact

- Specs: `outbound-http-clients`, `responses-api-compat`.
- Code: `CodexClient`, Responses HTTP adapter, contract and wire tests.
- No new dependency, setting, wire message, or operator action. Compact and
  other buffered operations remain outside this slice.
