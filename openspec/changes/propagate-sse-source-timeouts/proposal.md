# Propagate source timeouts through SSE keepalives

## Why
An upstream TimeoutError is mistaken for the keepalive timer expiring. Rewaiting on the already-failed chunk task emits heartbeats without suspension and can freeze the entire proxy event loop.

## What Changes
Distinguish expiry of the injector's own timer from an exception raised by the source. Preserve idle heartbeats, cancellation and source closure.

## Impact
- Affected specs: responses-api-compat
- Affected code: app/core/utils/sse.py

## Rust dependency audit correction
The published timeout correction's Rust CI audit reported RUSTSEC-2026-0382,
RUSTSEC-2026-0386, RUSTSEC-2026-0391, RUSTSEC-2026-0398,
RUSTSEC-2026-0403, and RUSTSEC-2026-0410. Resolve these without changing
SSE behavior or ignoring advisories: pin Tokio to 1.51.6 and update the
lockfile to rustls-webpki 0.103.16 and the patched zstd family (zstd 0.14.3,
zstd-safe 8.2.0, zstd-sys 2.1.2). compression-codecs 0.4.40 is the first
compatible adapter release using zstd 0.14 and zstd-safe 8; it requires
compression-core 0.4.33. Tokio also requires its compatible macro update.
Keep all other workspace dependency pins and the existing compression features.
These audit failures are not evidence of an operational incident's cause.
No normative SSE requirement changes are introduced by this dependency repair.
