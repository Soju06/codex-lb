# Change: Attest Codex egress observer parity

## Why

The traffic parity report currently compares the client-facing protocol, TLS,
identity, SSE, and WebSocket dimensions but leaves source IP/ASN entirely
outside the capture. A capture proxy can safely prove which source address it
observed, but only when the operator also identifies the observation boundary.
Without that boundary, equal loopback or private addresses can be mistaken for
the public address that OpenAI observed.

## What Changes

- Add an optional, operator-supplied capture observer id and role to the
  mitmproxy capture addon.
- Store only a digest of the observer id and source host, plus non-sensitive
  address-family metadata; never store a raw source address.
- Compare the observed A/C source only when both captures attest the same
  observer boundary and role.
- Report missing attestation as unobserved instead of pass, and distinguish an
  intercept-observer result from an origin-observer result.
- Keep public IP/ASN outside the claim unless an actual controlled origin is
  declared as the observer.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic capture addon, A/C comparison, Markdown report, tests,
  and operator documentation
