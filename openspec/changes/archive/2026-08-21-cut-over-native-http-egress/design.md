## Context

The previous parity change introduced a one-request Rust helper pinned to the network stack found in the installed Codex release family. A live unauthenticated probe negotiated HTTP/2, but application traffic was intentionally not cut over. Direct Responses streaming currently enters `aiohttp.ClientSession.post`, and direct model discovery enters the shared aiohttp session. Routed calls have additional endpoint-fallback and credential-sanitization invariants and are not a safe first cutover.

## Goals / Non-Goals

**Goals:**

- Send direct model discovery and direct Responses HTTP/SSE through the native helper when it is packaged.
- Keep startup and requests working without Rust or a helper executable.
- Prevent a fallback replay after dispatch becomes possible.
- Preserve downstream SSE/error behavior, cancellation, rate-limit ingestion, circuit breaking, and archives.
- Make the official Linux container the first zero-configuration packaged runtime.

**Non-Goals:**

- Native WebSocket egress.
- Native egress through account proxy pools and same-pool endpoint fallback.
- A new dashboard or environment configuration surface.
- A claim of full indistinguishability; source IP/ASN, connection reuse, routed traffic, and WebSocket TLS remain separate.

## Decisions

- Runtime discovery checks only for the fixed `codex-lb-native-egress` executable on `PATH`. It does not build source, scan arbitrary paths, or turn absence into a startup failure.
- The Docker image builds the locked Rust crate in a dedicated stage and installs the resulting executable on the runtime `PATH`. Source and wheel installs without that artifact retain aiohttp behavior.
- A native response implements the same minimal streaming surface consumed by the SSE parser: status, case-insensitive headers, single-consumer byte iteration, buffered error-body reads, and cancellation-safe close.
- Direct Responses creates one native request with the already-normalized body and headers. `NativeEgressUnavailable` before dispatch falls back inside the same attempt. Any protocol, timeout, transport, or body-stream error after the helper starts is reported through the ordinary attempt failure path and is not silently replayed through aiohttp.
- Model discovery is idempotent, but follows the same conservative unavailable-only fallback rule so transport selection remains observable and deterministic.
- This slice retains the one-process-per-request helper. A persistent multiplexed client and connection-pool parity require separate load/capture evidence and are not prerequisites for proving the first direct HTTP/2 cutover.

## Risks / Trade-offs

- [One-shot helpers do not reproduce Codex connection reuse] -> Report connection reuse separately and schedule persistent multiplexing only after correctness and cancellation evidence.
- [A helper crash could tempt an unsafe POST replay] -> Fall back only on the explicit unavailable-before-dispatch exception; all other failures stay terminal.
- [Native errors could bypass existing stream semantics] -> Adapt the response to the existing SSE parser and map native errors at the same outer attempt boundary.
- [Container builds become multi-language] -> Use a locked, isolated Rust build stage and copy only the release executable into runtime.
- [Wheel users may expect identical behavior] -> Keep absence safe and explicit in logs/reports; do not ship a platform binary inside a universal Python wheel.
