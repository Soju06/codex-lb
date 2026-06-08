## Why

Dashboard `sticky_thread` routing can stay pinned to an `ACTIVE` account that is already above the sticky reallocation budget threshold, because `STICKY_THREAD` is missing from the sticky budget-pressure guard in `LoadBalancer._select_with_stickiness`.

With the dashboard "Sticky threads" toggle enabled, this can keep routing requests to an exhausted pinned account until the user manually pauses it.

## What Changes

- Include `sticky_thread` in proactive sticky reallocation when the pinned account is above the configured budget threshold and a healthier candidate exists.
- Keep existing behavior below the threshold.
- Preserve the existing no-thrash behavior when every candidate is also above the threshold.
- Add focused regression tests for those three cases.

## Impact

- Sticky-thread mappings may rebind before the pinned account hard-fails on short-window exhaustion.
- No change to `prompt_cache`, `codex_session`, no-sticky routing, or sticky-session persistence semantics.
