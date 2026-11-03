## Why

An accepted usage reset credit can leave an account excluded until an operator sends Force probe. A fresh reopened weekly window can still leave the persisted rate-limit badge and runtime cooldown unchanged. Upstream issues Soju06/codex-lb#676 and #677 document lazy limiter refresh.

## What Changes

- Share a bounded, account-pinned wake and usage refresh across reset consumption paths.
- Recover quota blocks only on verified window reset evidence, preserving newer failures and hard blocks.
- Support secondary-only quota recovery and align standard quota badge evaluation with routing.
- Wake an elapsed, still-exhausted window at most once per boundary using durable claims.

## Impact

Accounts, usage refresh, reset-credit APIs, and quota status projection change. Routing strategy, sticky affinity, proxy bindings, and response schemas remain unchanged. No deployment is included.
