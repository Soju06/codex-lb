## Context

Compact SSE framing is native, but Python still parses each framed event and
collects indexed and unindexed output before returning the terminal response.
Python also owns public shape normalization, errors, routing, and archives.

## Decisions

- Introduce `codex-lb-responses`, a synchronous library without networking or IPC
  dependencies. Egress composes its compact collector with the existing framer.
- Add `collect_compact` to SSE options, requiring content-type-aware framing and
  negotiated `http_compact_collect_v1`. Raw JSON success and HTTP errors retain
  the raw-body path. No ordinary streaming or WebSocket behavior changes.
- Send a `compact` result as UTF-8 text fragments with the same 16 KiB bound as
  SSE fragments. The envelope distinguishes completed response, upstream terminal
  error, and invalid/missing completion. Python parses this one envelope and
  uses the existing public error translation and payload normalization.
- Stop reading immediately on a terminal event, including when more bytes or an
  oversized event follow in the same body read. Emit transport `end` outside the
  cancellable HTTP execution future as with existing requests.
- Preserve unknown response/item fields as raw JSON. This avoids lossy conversion
  of large integers and unnecessary interpretation of application payloads.
- Python collection remains only for the supported missing-helper transport.
  Native requests never replay or invoke the Python collector after dispatch.

## Risks / Trade-offs

- Collector semantic drift: shared fixtures run through both the Rust collector
  and the existing Python implementation, plus direct/routed real-helper probes.
- Large collected outputs: retain the existing aggregate behavior; bound each
  IPC line and keep cancellation/queue bounds. No new public aggregate limit.
- Helper version skew: require the new capability before dispatch and ship the
  adapter and helper together. Incompatible installed helpers fail closed.

## Migration Plan

Cut over only successful compact SSE collection. Verify exact output precedence,
missing completion, terminal errors, timeouts, cancellation, and cleanup before
archiving. Future Rust application code can directly reuse this library.
