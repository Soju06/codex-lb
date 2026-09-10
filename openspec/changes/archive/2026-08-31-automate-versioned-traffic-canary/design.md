# Design

## Failure contract

Each scenario is reconstructed from the existing comparison JSON. The gate
requires equal non-zero A/B attempt counts, compatible A/B turns, a compatible
final outcome, and a versioned expected final relation. Success, 429, and 503
also require the ordinary strict B/C semantic gate. Intentionally incomplete
scenarios are not required to become strict semantic passes; their end-to-end
failure/recovery contract is the gate. B/C translation stays visible but does
not override an exact A/B client-visible result.

The baseline contains only bounded enums, statuses, attempt counts, and
relations. It contains no request or response content.

## Version-aware scheduling

A runner reads the current `codex --version`, holds an exclusive non-blocking
lock, and evaluates two triggers: the current version differs from the last
successful version, or the last successful run is at least seven days old. It
invokes one configured argv without a shell, passes a new scratch run directory
and trigger metadata through environment variables, and writes state
atomically only after exit 0. Missing configuration, timeout, overlap, or
command failure cannot advance the success state.

The user timer invokes the checker daily with randomized delay. This catches a
version update within one day while avoiding a full daily run.

## Fast versus full evidence

The scheduled suite runs the controlled raw HTTP/2 A′/A/C probe and the seven
failure scenarios. This is the high-ROI drift signal and performs no OpenAI
model inference because the controlled origins answer locally. It is labelled
`fast_canary`, not `full_composite`.

Repeated TLS distribution needs at least 20 independent ClientHellos in each
of three cohorts for HTTP JSON, SSE, and WebSocket and is materially more
expensive. It remains a release/monthly or TLS-stack-change attestation and is
combined with the fast evidence by the existing full composite gate.

## Storage and cleanup

Raw canary output is placed under `/mnt/scratch/bench/codex-traffic-parity/`.
Code/config/result summaries live in the approved home categories. Each run
uses a new isolated directory. Database files, encryption keys, bootstrap logs,
and transient certificates are removed before success is reported. Retained
metadata captures and compact reports are privacy scanned.
