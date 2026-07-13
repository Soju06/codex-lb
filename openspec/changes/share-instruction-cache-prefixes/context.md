# Shared Instruction Cache Context

## Purpose

Reduce repeated cache writes for the stable Codex instruction and project
context prefix without introducing a wrapper script or conflating prompt-cache
locality with conversation identity.

## Decisions

- The client-supplied `prompt_cache_key` remains the local session and bridge
  identity. A wire-only key is used for upstream prompt caching.
- The wire key hashes the exact model plus all request content through the
  stable boundary. Consequently different models never share a key.
- The stable boundary is the first user message when another user message
  follows it. In Codex requests this is the contextual AGENTS/environment
  message before the current user task. Requests without that structural
  boundary are forwarded unchanged.
- The feature is enabled by default and can be disabled with
  `CODEX_LB_SHARED_PROMPT_CACHE=false`.
- Existing client-authored explicit breakpoints are preserved; CodexLB does not
  add a second policy on top of them.
- GPT-5.6 receives an explicit breakpoint. Older models receive the shared key
  but retain their existing automatic prompt-caching behavior because they
  reject explicit breakpoints.

## Constraints and failure modes

- The public GPT-5.6 Responses API supports `prompt_cache_options`, but the
  ChatGPT `/backend-api/codex/responses` surface currently rejects that field
  with `400 Unsupported parameter`. CodexLB therefore adds only the supported
  content-block breakpoint marker and relies on the backend's default lifetime.
- Because implicit mode cannot be disabled on this backend, the latest message
  may still create rolling cache writes in addition to the shared breakpoint.
- Cache contents remain account-local upstream. A load-balanced account must be
  warmed independently, even though every account receives the same derived
  key for the same model and prefix.
- On models with paid cache writes, the first write is a net cost unless a
  later matching request reads the prefix before eviction. Same-thread replies
  were already cacheable; the added benefit is primarily reuse across threads.

## Example

Two Codex threads send identical base instructions and project context but
different current tasks. Their local thread keys remain `thread-a` and
`thread-b`. CodexLB forwards both Sol requests with the same derived wire key
and marks the end of the shared project-context message. A Terra request hashes
to a different wire key.
