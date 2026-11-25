# Context

This is the request compatibility slice of #1953 on current main. It preserves the accepted candidate implementation without requiring the quarantine-generation sibling. A short raw string must remain short after validation; two function-call outputs need the anchor holding their calls.

Mixed-version forwarding carries authenticated input-shape metadata, checks the advertised owner process, and uses existing local recovery for eligible pre-dispatch rejections. Whole-scope owner approval remains required. These policies are extracted, not newly approved here.

Current-head review reproduced a missing anchor for one 4096-character function-call output on the public Responses route. Tool-output-only classification therefore covers nonempty arrays of any size. Legacy receiver semantics remain unchanged; the existing classification-disagreement gate now fences this case too.

## Signing compatibility and rollout

PR 2088 provenance is additive: it signs the authenticated body proof selected
by the forwarding path and does not require changing either known
pre-provenance codec. The two codecs are the public pre-input-shape V2 envelope
and the shape-V2 envelope already deployed at `9ede3db64fbb71300aa934b767a4f4db79d4a5b4`.
Treating only the first as “the deployed codec” caused the original composite
report to conflate PR 2088 provenance with PR 2277's older rolling gap.

The repaired wire keeps the public V2 envelope in
`x-codex-bridge-signature-v2` and moves the byte-identical deployed shape-V2
envelope to `x-codex-bridge-input-shape-signature-v2`. A new receiver accepts
old `9ede3db` origins that still carry those shape-V2 bytes in the old header.
The reverse direction is not symmetric for file-bound or epoch-gated requests:
an old `9ede3db` owner does not know the new header, and primary fallback is
correctly unavailable. Therefore `9ede3db -> repaired build` requires an
all-owner stop/start cutover. The current SQLite deployment has ring size one,
so that boundary is operationally available; it is not a supported mixed-owner
rolling boundary. Public pre-input-shape owners remain compatible with ordinary
new forwards, including file-bound forwards, through the preserved V2 proof.

For example, an ordinary file forward carries public V2 plus exact-shape V2.
A public predecessor verifies the first; a repaired owner verifies the second.
An epoch-gated ambiguous continuation carries only exact-shape V2, so any owner
that cannot authenticate the marker and epoch fails closed before continuity
selection.
