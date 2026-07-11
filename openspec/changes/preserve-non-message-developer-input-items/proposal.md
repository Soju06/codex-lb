# Preserve Non-Message Developer Input Items

## Summary

Stop dropping non-message `input` items with a `system`/`developer` role when
hoisting instruction messages into the `instructions` field.

## Motivation

The instruction-hoisting normalizer treated every `system`/`developer`-role
input item as an instruction message. Items that carry no `content` (such as
the Codex responses-lite `{"type": "additional_tools", "role": "developer",
"tools": [...]}` prefix) contributed no instruction text, produced no preserved
item, and were silently removed from `input`. Upstream then received a
well-formed request with no tools anywhere, and the model responded that no
terminal/filesystem tool was exposed (issue #1157).

The merged fix for #1157 preserved the `additional_tools` case by skipping
normalization entirely for Lite-shaped requests. But the underlying hazard is
broader: any future non-message typed item that upstream introduces with
`role: developer/system` and no `content` would still be folded away in a
non-Lite request, reproducing the same class of bug (issue #1171). Two of the
duplicate fixes for #1157 (#1159, #1158) carried a more general guard — never
fold a `system`/`developer` input item whose `type` is present and not
`"message"` — which is better defense-in-depth.

## Scope

- Only hoist input items that are actual messages (`type` omitted or
  `"message"`) into `instructions`.
- Pass every other typed `system`/`developer`-role input item through to
  upstream untouched, byte-for-byte and in its original position.
- Applies to both `ResponsesRequest` and `ResponsesCompactRequest`
  normalization, at validation time and on upstream serialization
  (`to_payload()`).

## Out of Scope

- Changing how message-shaped instruction items are hoisted or merged.
- Changing the Responses Lite `additional_tools` whole-request preservation
  rule: requests containing an `additional_tools` item still skip instruction
  hoisting entirely so the native Lite input prefix stays byte-for-byte intact.
- Modeling unknown item shapes; they are forwarded opaquely to stay
  codex-faithful.
