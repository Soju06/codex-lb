## 1. Policy

- [x] 1.1 Validate configuration_update items against allowed/enforced efforts
- [x] 1.2 Prepend a leading enforced-effort update on anchored continuations; allowed-list keys do not synthesize resets
- [x] 1.3 Reject unsupported Astra update shapes, adjacent updates, compact, auto truncation/compaction
- [x] 1.4 Map Ultra to Max only at subscription to_payload serialization

## 2. Call sites

- [x] 2.1 Subscription HTTP stream/collect/compact/chat-completions
- [x] 2.2 Source Responses: key policy without Astra schema restrictions
- [x] 2.3 HTTP-bridge dispatch uses prepared input while completion bookkeeping retains the client prefix
- [x] 2.4 Select the WebSocket Astra schema after recorded subscription ownership resolves, before admission

## 3. Verification

- [x] 3.1 Unit and integration regressions for bypass, continuation reset, Ultra identity, source contract
- [x] 3.2 Strict OpenSpec validation of this change
- [x] 3.3 Refresh stored client-plane update efforts after each injected-anchor continuation
- [x] 3.4 Preserve original HTTP full-resend bookkeeping while validating the trimmed continuation; prove streaming and collect routes retain durable prefix matching on a later resend
- [x] 3.5 Real-route subscription-owner regression and source/subscription/key-policy compatibility controls on canonical and equivalent sockets
- [x] 3.6 Validate the selected WebSocket continuation and its preserved stale-anchor full-resend fallback after subscription ownership overrides a model source
- [x] 3.7 Preserve terminal SSE policy errors and reservation cleanup after late HTTP-bridge anchor injection without reinstating removed recovery modes
- [x] 3.8 Keep schema selection and owner routing consistent across concurrent owner publication, and re-resolve on the next request
- [x] 3.9 Validate update ordering after subscription input normalization, including deduplication and serialization, preserving valid separated updates
- [x] 3.10 Preserve source configuration updates without reasoning changes while enforcing explicit efforts on restricted keys
- [x] 3.11 Preserve source-owned reasoning fields during effort serialization and API-key reservation estimation
- [x] 3.12 Preserve client prefix metadata after a late ledger anchor and prove subsequent full-resend matching through the real HTTP bridge
- [x] 3.13 Use the source-bound body for the existing reservation estimate and cover padded effort values with overlapping real-route requests
- [x] 3.14 Validate subscription update shape before update key policy and preserve source/request-level policy behavior
- [x] 3.15 Apply the existing HTTP continuation preparation to eligible pre-submit raw fallback and verify stream/collect, replay trimming and Ultra identity
- [x] 3.16 Drop the local Astra effort enumeration, accept `disabled`/`none`, prepend only for enforced keys, and gate non-bridge HTTP trim on `gpt-6-astra`
- [x] 3.17 Preserve anchored-delta client prefix metadata when preparation inserts a reset without trimming; verify the surviving ledger-publication race and subsequent full resend
- [x] 3.18 Record that an enforced reset makes automatic truncation or compaction continuations fail closed while allowed-list keys complete them
- [x] 3.19 Forward non-Ultra configuration-update efforts verbatim at subscription serialization and keep the client's exact strings for late-anchor restoration; cover HTTP and WebSocket routes
