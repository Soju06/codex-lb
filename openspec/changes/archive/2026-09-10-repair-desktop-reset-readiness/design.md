# Design

Keep the native relay, identity validation, usage summary and reset routes together so the normal Desktop Reset action works from main. Merge main's native usage egress behavior with the existing original-envelope parser.

Read and detach an eligible account inside the inventory repository scope, then close that scope before authentication. Use BackgroundAccountsRepository for token refresh so each read or write owns a short database session even when the shared refresh outlives a cancelled caller. Resolve the upstream route and fetch credits without retaining the initial session. Existing post-refresh membership and pre-consumption eligibility checks still govern selection and redemption.

Translate permanent helper-ledger disagreement into the established 409 conflict response. Keep the aggregate inventory deadline; moving it behind the semaphore would silently change the documented latency contract.

Validate the migration test's explicit disposable database before constructing an engine. Remove its dependency on global test reset because that dependency previously executed first. CI and the test Makefile default use the guarded database name. This changes only disposable verification setup.
