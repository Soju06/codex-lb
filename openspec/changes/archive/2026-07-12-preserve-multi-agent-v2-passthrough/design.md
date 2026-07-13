# Design

## Request-scoped metadata projection

An HTTP request can be multiplexed onto an already-open upstream WebSocket, so
the socket handshake is not a request-scoped metadata channel. When preparing
each `response.create` frame, project nonblank values from
`x-codex-turn-metadata`, `x-openai-subagent`,
`x-codex-parent-thread-id`, and `x-codex-window-id` into
`client_metadata`.

The request body remains canonical: use insert-if-absent semantics so a value
already present in body `client_metadata` is not replaced by its compatibility
header. Header names are matched case-insensitively. Installation identity
continues to use the existing account-owned replacement path.

## Compact affinity

`x-codex-turn-state` is more specific than a session header or prompt-cache
key. Compact classification mirrors the normal Responses classifier: resolve
the prompt-cache key as before, then return Codex-session affinity for a
nonblank turn-state before considering session or cache affinity. Resolving the
cache key first preserves the existing derived-cache-key side effect used for
upstream payload consistency.

Observability labels the resulting source as `turn_state_header`, matching the
Responses path instead of reporting it as a generic session header.

## Namespaced side-effect identity

The Responses API represents multi-agent v2 calls with separate `namespace`,
`name`, and `call_id` fields. Downstream deduplication therefore adds namespace
to every cache key. For a namespaced call, cross-response replay identity also
retains `call_id`, as already done for code-mode `exec` and `collaboration`
calls. An exact replay with the same namespace and call ID remains suppressed
even when a different call was emitted earlier in the new response; a
different namespace or call ID remains visible.

History deduplication follows the same boundary. Namespaced call keys retain
namespace and call ID so distinct calls and their matching outputs survive.
Stable namespaced identities remain tracked across intervening side-effect,
read-only tool, ordinary assistant-output, and unmatched tool-output items
until an explicit developer, system, or user input segment boundary. Those
different side-effect, read-only tool, and unmatched tool-output items clear
only legacy argument-based entries; ordinary assistant output preserves the
existing consecutive legacy state. Flat legacy side-effect calls continue to
use consecutive argument-based replay identity and reset at those legacy tool
boundaries, preserving protection for reconnects that replay shell, patch, or
terminal operations under a new call ID without collapsing intentional later
repetitions.

Nested `multi_tool_use.parallel` behavior is unchanged. Its existing entries
use an explicit empty namespace slot in the expanded cache-key shape.

## Failure boundaries

This change does not weaken exact-replay suppression, previous-response
continuity checks, API-key enforcement, or account ownership. It changes only
metadata projection, affinity classification, and the identity of namespaced
side-effect calls.
