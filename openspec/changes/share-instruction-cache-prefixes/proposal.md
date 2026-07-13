# Change: Share instruction cache prefixes

## Why

Codex assigns each agent thread a distinct `prompt_cache_key`. CodexLB also
uses that client key to isolate HTTP bridge lanes, so replacing it globally
would risk cross-thread conversation reuse. Forwarding it unchanged, however,
prevents separate threads with identical stable instructions from using the
same reliable prompt-cache match.

## What Changes

- Enable shared instruction-prefix caching by default with a concise setting.
- Preserve the client key for local affinity and bridge identity.
- Derive a separate upstream key from the exact model and stable request prefix.
- Add an explicit cache breakpoint for GPT-5.6 while older models continue to
  use automatic prompt caching.
- Leave OpenAI-style routes and unsupported request shapes unchanged.

## Impact

- Every exact model uses an independent upstream key even for identical text.
- New Codex threads in the same project can reuse a warm stable prefix while
  remaining isolated as conversations.
- On paid-write models, savings require at least one later matching request
  before the cache is evicted; a prefix written only once can cost more.
- The ChatGPT Codex backend still applies its implicit latest-message caching;
  this change cannot disable those rolling writes.
