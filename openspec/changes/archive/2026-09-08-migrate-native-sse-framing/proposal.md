# Change: Migrate direct native Responses SSE framing to Rust

## Why

The Rust HTTP worker currently serializes network chunks for Python to decode,
buffer, scan, and split into SSE blocks. Moving that transport responsibility
to the existing worker is the next bounded migration slice and avoids another
process boundary for each chunk.

## What Changes

- Negotiate `http_sse_v1` and add optional per-request SSE framing limits.
- Frame successful direct streaming Responses bodies in Rust, including byte
  limits and body-read idle deadlines; send complete text blocks over IPC.
- Consume these blocks without Python byte framing, retaining downstream
  normalization, terminal detection, archives, routing, and retry ownership.
- Preserve raw bodies for HTTP errors and non-streaming requests, and preserve
  Python framing for transport paths outside this slice.
- Verify shared cross-language fixtures and the actual worker/proxy boundary.

## Impact

- Specs: `outbound-http-clients`, `responses-api-compat`.
- Code: protocol and egress crates, Python native adapter, direct Responses path.
- No new operator setting, dependency, deployment action, or public API.
