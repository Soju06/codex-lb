# Fix Cursor Chat Completions Compatibility

## Why

This PR already updates compatibility behavior for Cursor/OpenAI-style chat traffic:

- `/v1/chat/completions` now supports Responses-shaped payloads (with `input` and
  optional empty `messages`) on the passthrough path.
- Built-in tool and strict schema handling was adjusted for Responses-shaped
  passthrough while keeping existing chat-message restrictions for regular chat
  payloads.
- Cursor GPT-5 compatibility aliases are normalized to canonical model slugs, and
  API-key allowlists and model visibility honor the canonicalized values.

Those are externally visible behavior changes, so this OpenSpec change set is
required before merge.

## What Changes

- Document the new chat completions compatibility acceptance for `input`-based payloads.
- Document alias-aware model allowlist and model-list normalization behavior.
- Document Cursor-style GPT-5 alias normalization in responses compatibility.
- Document that built-in/Responses-style tools are preserved only on the
  Responses-shaped chat path.

## Impact

- Clients using Cursor/OpenAI-compatibility patterns can rely on consistent model
  alias handling and tool compatibility between `/v1/chat/completions` and
  `/v1/responses`.
- API-key allowlists and model catalogs now align with alias canonicalization so
  access checks and discovery are consistent.
