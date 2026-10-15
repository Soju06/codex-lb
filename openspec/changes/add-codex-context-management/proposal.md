# Support native Codex history and notes across account pools

## Why
Native Codex clients send history and notes operations to endpoints missing from codex-lb. Their contents belong to individual upstream accounts, so ordinary inference rotation cannot by itself preserve access to previous context.

## What Changes
- Add the ten explicit authenticated native history and notes routes.
- Bind each session to exactly one immutable proxy API key and notes owner, and record its history participants.
- Keep notes on their owner and gather encrypted history from recorded participants; authenticate proxy envelopes before restoring native results to inference.
- Allow history-enabled forks to replay already-issued context results under the same API key and current account scope, without granting access to the original session for later operations.
- Preserve the deployed context migration ancestry and join it to current main with an explicit merge revision.
- Preserve existing replay restrictions for all unverified account-specific state.
- Accept context operations in dashboard request logs.
- Integrate with current main's clock/scheduler seams and migration head.
- Avoid parsing full frames again and cache confirmed dispatch ownership and participants to remove repeated context writes.

## Impact
Proxy routes, context bookkeeping, replay classification, dashboard validation and one migration. No new configuration setting. OpenAI still stores note and history content.

Related alternative: #2101 pins history-marked inference to a single owner without proxy envelopes. Choosing between that design and account-pool context support remains a maintainer decision; this PR retains multi-account inference.
