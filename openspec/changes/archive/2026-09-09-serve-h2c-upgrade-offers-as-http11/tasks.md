## 1. Implementation

- [x] 1.1 Neutralize non-WebSocket upgrade offers in the httptools HTTP
      protocol: replay the request head without the declined offer's
      hop-by-hop headers and serve the request as plain HTTP/1.1.
- [x] 1.2 Wire the tolerant protocol into the server bootstrap, falling back
      to an h11 subclass with the same header hygiene when httptools is
      unavailable (stock h11 already delivers the body but exposes the
      declined offer's headers).
- [x] 1.3 Classify upgrade offers by combining repeated `Connection` fields
      (RFC 9110 §5.3) so a second `Connection: keep-alive` field cannot hide
      the offer and reproduce the body loss.
- [x] 1.4 Replay declined offers iteratively (loop in `data_received`) rather
      than recursively, so a segment pipelining many upgrade-offering requests
      cannot drive attacker-controlled recursion depth (RecursionError
      escaping into the event loop) or pin per-frame byte copies.

## 2. Validation

- [x] 2.1 Add transport-level regressions: h2c offer with coalesced
      header/body and with split segments both reach the application with the
      full body and succeed; the declined offer's headers are not exposed; the
      connection stays reusable.
- [x] 2.2 Add a live-server regression over real sockets using the production
      protocol wiring, including a real WebSocket upgrade that must keep
      completing, plus a canary pinning the stock uvicorn defect.
- [x] 2.3 Add a regression pipelining 2000 h2c offers in one segment: all are
      served and the connection survives (raised RecursionError when the
      replay was recursive).
- [x] 2.4 Run focused tests, lint, type checks, and strict OpenSpec
      validation.
