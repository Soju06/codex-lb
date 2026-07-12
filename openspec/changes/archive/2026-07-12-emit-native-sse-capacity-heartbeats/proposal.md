## Why

Image-capable native Codex requests bypass the HTTP responses bridge and wait
for local account capacity in the direct streaming path. That route propagates
HTTP errors but disables the OpenAI SDK stream contract, so suppressing
keepalives solely because error propagation is enabled leaves Codex with no SSE
bytes during a recoverable wait and allows its startup idle timeout to fire.

The earlier local-cap change described keepalive suppression too broadly for
all propagated startup errors. The actual compatibility boundary is whether the
downstream stream must obey the OpenAI SDK contract.

## What Changes

- Emit `codex.keepalive` during direct account-capacity waits when the OpenAI
  SDK stream contract is disabled, even if HTTP errors are propagated.
- Preserve the no-keepalive startup behavior when HTTP errors are propagated
  under the OpenAI SDK stream contract.
- Reconcile the active local-cap wording with that contract-mode boundary and
  add a native image-bypass regression.

## Impact

- Native Codex image-capable requests remain alive while local stream or
  response-create capacity recovers.
- OpenAI SDK-compatible routes retain structured startup 429 behavior without
  receiving non-standard `codex.*` events.
