# Codex model-catalog required-field compatibility

## Decision

The final Codex catalog mapper is the compatibility boundary. It fills fields
that are absent, so the repair also covers metadata restored from an older
persisted registry snapshot without rewriting the snapshot or replacing newer
wire-valid upstream values.

`experimental_supported_tools` defaults to an empty list. Bundled models use
their known truncation mode: `gpt-5.2` uses the upstream-compatible 10,000-byte
limit, while the other bundled Codex models use the upstream-compatible
10,000-token limit. Other missing policies use the same 10,000-token default as
codex-lb's Responses-capable model-source catalog. Explicit upstream or
operator-provided policies take precedence unchanged.

Model-source metadata is an operator-extensible JSON object. Its existing tool
capability reader accepts mixed `experimental_supported_tools` lists and
ignores non-string members. Catalog rendering follows that same compatibility
rule: for example, `["custom", 42, {"type": "bad"}]` becomes `["custom"]`,
while a non-list value becomes `[]`. This keeps one malformed optional value
from turning the atomically decoded catalog into a 500 response.

The same boundary validates explicit truncation policies. Operator metadata
such as `{"truncation_policy": null}` or an object missing `limit` falls back
to the model-compatible byte/token policy described above. Valid complete
policies, including forward-compatible extra fields, remain authoritative.

Codex's upstream protocol defines `TruncationMode` as the closed snake-case
`bytes | tokens` enum and `limit` as an `i64`. The mapper mirrors that wire
shape exactly: it rejects coercible-but-invalid JSON values such as a numeric
string and falls back when an integer is outside the signed 64-bit range.

## Failure mode

Codex parses the complete `models` array atomically. For example, a visible
`gpt-5.6-sol` entry can be complete while a hidden retained `gpt-5.2` entry lacks
`truncation_policy`; Codex then rejects the whole response at the hidden entry
and reports that model refresh could not be decoded.
