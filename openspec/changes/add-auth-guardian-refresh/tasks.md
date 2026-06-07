## 1. Auth Guardian

- [x] 1.1 Add configurable Auth Guardian settings with conservative defaults.
- [x] 1.2 Add a leader-election guarded background scheduler using background DB sessions.
- [x] 1.3 Select only `active` accounts whose `last_refresh` is older than the configured max age.
- [x] 1.4 Force-refresh accounts with bounded batch size, bounded concurrency, jitter, and backoff.
- [x] 1.5 Log only account id/safe account alias and error code/message, never token material.
- [x] 1.6 Wire scheduler startup/shutdown in `app/main.py`.

## 2. Verification

- [x] 2.1 Add unit tests for candidate selection, forced refresh, leader-election skip, and backoff.
- [x] 2.2 Run focused backend unit tests and lint.
- [ ] 2.3 Run `openspec validate --specs` when the CLI is available.
