# Codex model-catalog required-field compatibility

## Decision

The final Codex catalog mapper is the compatibility boundary. It fills only
fields that are absent, so the repair also covers metadata restored from an
older persisted registry snapshot without rewriting the snapshot or replacing
newer upstream values.

`experimental_supported_tools` defaults to an empty list. Bundled models use
their known truncation mode: `gpt-5.2` uses the upstream-compatible 10,000-byte
limit, while the other bundled Codex models use the upstream-compatible
10,000-token limit. Other missing policies use the same 10,000-token default as
codex-lb's Responses-capable model-source catalog. Explicit upstream or
operator-provided policies take precedence unchanged.

## Failure mode

Codex parses the complete `models` array atomically. For example, a visible
`gpt-5.6-sol` entry can be complete while a hidden retained `gpt-5.2` entry lacks
`truncation_policy`; Codex then rejects the whole response at the hidden entry
and reports that model refresh could not be decoded.
