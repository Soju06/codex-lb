# Daily reset schedule

The reset contract is defined in [Weekly token usage reset](spec.md#requirement-weekly-token-usage-reset).

Daily API-key limits follow the Ho Chi Minh City calendar using the operator-requested fixed UTC+7 offset. Persisted timestamps remain naive UTC; the host timezone does not affect calculation. Other limit windows retain their existing durations.

For example, a daily limit created at `2026-09-06 16:59 UTC` resets at `2026-09-06 17:00 UTC`, which is `2026-09-07 00:00` in Ho Chi Minh City. A daily limit created exactly at `17:00 UTC` receives the following day's `17:00 UTC` reset timestamp.

The existing leader-gated alignment pass runs at `16:50 UTC` (`23:50` local time), ten minutes before the reset boundary. It moves existing daily reset timestamps while preserving usage counters. Expired counters clear through request-time lazy expiry or the hourly background fallback; the alignment pass itself does not clear counters. Existing strict expiry comparisons process a limit once the clock is past its reset timestamp.

No database migration or host timezone change is needed. Fresh and explicitly reset limits use the local boundary immediately after deployment. Existing future timestamps converge at the next alignment pass, while expired timestamps converge when reset. Deploying after `16:50 UTC` can leave legacy timestamps until the next day's pass. Alignment logs identify `Asia/Ho_Chi_Minh` midnight and record the target as UTC.
