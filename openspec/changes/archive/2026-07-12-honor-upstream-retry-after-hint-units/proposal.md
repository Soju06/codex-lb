# Honor upstream Retry-After hint units in account cooldown

## Problem

When an upstream Codex/ChatGPT response rate-limits an account (HTTP 429), its
message often carries a "try again in <duration>" hint. `parse_retry_after`
only recognized second and millisecond units, so any minute, hour, or compound
hint (for example `20m`, `6m0s`, `1h2m3s`) failed to match and returned `None`.
`handle_rate_limit` then fell back to `backoff_seconds(state.error_count)`, a
sub-second-to-few-second backoff, and set `cooldown_until` far earlier than the
upstream asked. The balancer re-selected the still-rate-limited account almost
immediately, re-sending traffic into the same 429 and amplifying the rate limit
instead of waiting the cooldown out.

## Solution

Teach `parse_retry_after` to parse the full contiguous run of `<number><unit>`
tokens after "try again in", summing hour, minute, second, and millisecond
components (and their word forms). A longest-match-first unit alternation keeps
`ms` distinct from `m` and `minutes` from `m`. When no token is recognized the
function still returns `None`, so `handle_rate_limit` keeps its existing backoff
fallback. The account then stays in cooldown for the duration the upstream
actually requested.

## Changes

- Parse minute, hour, and compound `<num><unit>` retry hints in
  `parse_retry_after`, in addition to seconds and milliseconds
- Sum compound durations (for example `1h2m3s`) into a single seconds value
- Preserve the `None` result for unparseable hints so the error-count backoff
  fallback in `handle_rate_limit` is unchanged
- Add unit coverage for minute, hour, compound, and word-form hints

## Out of scope

- Changing the `backoff_seconds` fallback schedule
- Changing how `reset_at` is extracted or clamped
- Changing the selector's user-visible "Try again in {N}s" hint ceiling
