# Design

## Evidence boundary

The observer is a TLS origin rather than a forward proxy. It receives the
client's plaintext HTTP/2 frames after TLS termination and therefore can parse
the connection preface and frame headers before passing the same bytes to the
`hyper-h2` state machine. It stores frame type, flags, stream id, payload
length, ordered SETTINGS pairs, flow-control increments, and a SHA-256 digest
for HPACK header-block fragments. It never stores HPACK bytes, decoded values,
DATA bytes, request bodies, socket peer addresses, TLS secrets, or certificate
private material.

## Deterministic origin behavior

The server accepts only negotiated ALPN `h2`, bounds connections, frames,
streams, compressed and decoded bodies, and supports the controlled `/models`
and `/responses` path aliases already used by the ordinary origin fixture.
Responses never reflect request content. Operators supply an explicit leaf
certificate and key; the fixture creates neither durable keys nor trust state.

## Comparison

The dedicated comparator aligns request records by occurrence and reports:

- exact ordered initial SETTINGS equality;
- exact pre-request connection-control frame shape;
- decoded header-name order and casing;
- stream-id and connection-reuse patterns;
- HPACK block fragment lengths and opaque digests as informational evidence.

A second direct capture A′ is accepted as a natural-variance reference. Missing
or incomplete evidence is unobserved, never pass. Exact HPACK digests are not a
gate because credentials and dynamic-table history can legitimately differ.

## Non-goals

- Decrypting captures from another TLS terminator.
- Persisting or comparing header values or request bodies.
- TCP packet sizing, congestion control, kernel timing, or public IP/ASN.
- WebSocket framing, which is already handled by the existing capture path.
