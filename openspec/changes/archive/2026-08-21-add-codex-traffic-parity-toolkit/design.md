## Context

The three capture legs have different comparison meaning:

- **Path A** is a direct-client baseline and commonly comes from a separate
  model invocation. Generated text, response ids, timing, and usage may differ.
- **Path B** is client to codex-lb.
- **Path C** is codex-lb to the selected upstream. Paths B and C describe the
  same invocation and therefore form the fidelity oracle, subject to explicit
  proxy transformations.

Codex Responses traffic can be a single JSON response, an SSE event stream, or
a long-lived WebSocket containing multiple `response.create` turns. A useful
analyzer must retain that distinction while projecting every form into common
request, event sequence, terminal state, tool, and usage fields.

## Goals / Non-Goals

**Goals:**

- Capture and identify HTTP JSON, HTTP SSE, and WebSocket traffic separately.
- Reconstruct multiple WebSocket turns from one connection.
- Make B/C fidelity failures machine-actionable and suitable for a strict CI or
  investigation exit code.
- Make direct A comparisons informative without declaring nondeterministic
  model output a proxy defect.
- Avoid credential leakage and minimize prompt/output persistence by default.

**Non-Goals:**

- Add packet-level TCP/TLS fingerprint comparison or decrypt traffic without
  an explicitly installed local interception CA.
- Prove semantic equivalence of two independently sampled model completions.
- Change proxy routing, transport selection, event rewriting, or production
  observability.
- Add mitmproxy to the codex-lb runtime dependency set.

## Decisions

1. **Transport remains first-class.** `http_json`, `http_sse`, and `websocket`
   are reported, not normalized away. A B/C transport change is visible even
   when both legs project to the same Responses events.

2. **One common turn projection.** HTTP records map one request/response pair
   to one turn. WebSocket client `response.create` frames open turns, and
   upstream events attach through `response.completed`,
   `response.incomplete`, `response.failed`, or `error`.

3. **B/C is strict; A is a baseline.** Missing turns, incompatible request
   structure, event lifecycle changes, terminal changes, and same-run
   usage/tool discrepancies are hard B/C mismatches. Path A supplies model,
   transport, header, event-shape, and capability context but does not require
   exact ids, timing, text, token counts, or event multiplicity.

4. **Volatile wire fields are normalized narrowly.** Response/request ids,
   sequence counters, timestamps, and latency are excluded from structural
   equality. Model, reasoning effort, service tier, tool names/types, event
   ordering, terminal state, and usage remain observable.

5. **Safe capture defaults.** Authorization, API-key, cookie, and proxy
   credential headers are always replaced. Metadata mode hashes sensitive text
   values with their byte length so equality can be checked without retaining
   raw prompts, tool arguments, encrypted reasoning, or generated text. Full
   body capture requires an explicit option and is documented as sensitive.

6. **Optional capture dependency.** Parser, analyzer, and report modules use
   only the Python standard library. Only the addon imports mitmproxy.

## Risks / Trade-offs

- Metadata hashing still reveals equality and byte length. Captures remain
  sensitive diagnostics and stay ignored by git.
- One WebSocket can carry control or error frames outside a turn. The analyzer
  reports orphan frames instead of silently assigning them.
- Proxy transformations can be intentional. The report exposes normalized and
  raw structural differences; an operator must still classify a newly observed
  transformation before relaxing strict comparison.
- A direct baseline cannot prove content parity because it is a separate model
  sample. The strict oracle intentionally remains B versus C.

## Migration Plan

Tooling-only and additive. Remove the scripts, tests, guide, and delta to roll
back; no deployed state or data migration is involved.

## Open Questions

None.
