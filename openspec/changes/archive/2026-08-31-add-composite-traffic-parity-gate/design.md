# Design

## Composite evidence boundary

The gate consumes existing metadata-only captures; it does not capture traffic
or contact an upstream. Semantic B/C captures prove same-run transformation
fidelity. Independent A′/A/C TLS cohorts prove stable ClientHello capabilities
and extension-order distribution parity. Independent A′/A/C raw HTTP/2
captures prove direct repeatability and the maintained native wire profile.

The compact result stores SHA-256 digests and byte counts for its inputs. It
does not copy header values, bodies, HPACK fragments, WebSocket payloads, JA3
sample lists, or credential-bearing source records into the aggregate report.

## Strict policy

The default required semantic transports are HTTP SSE and WebSocket. HTTP JSON
model discovery remains covered by the TLS cohort and raw HTTP/2 profile; it is
not required as a Responses inference transport because the authenticated
Codex backend rejects non-streaming inference. Operators may override required
transport sets explicitly.

Strict success requires:

- zero hard B/C semantic mismatches and non-zero B/C coverage for every
  required semantic transport;
- a matching, sufficiently sampled TLS result for every required TLS
  transport;
- every stable A′/A and A/C raw HTTP/2 dimension to be observed and matching.

Missing or malformed evidence is a failure, never an implicit pass.

## DATA segmentation projection

Exact request sizes legitimately vary across independent Codex turns. The raw
HTTP/2 comparator therefore maps each DATA frame to `max` when its payload
equals the peer-advertised maximum frame size and `partial` otherwise, while
retaining the ordered END_STREAM and PADDED flag shape. GET/model requests have
an observed empty DATA sequence. This detects a different chunking policy
without retaining DATA bytes or comparing the variable final-frame length.

## Timing

HTTP durations and WebSocket flow spans are summarized by count and bounded
percentiles for diagnosis. They are informational because A and C are separate
process invocations and local scheduling, model latency, and fixture behavior
can dominate. A future timing gate requires repeated cohorts and an explicit
statistical budget rather than a one-run exact comparison.
