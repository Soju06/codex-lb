# Release rate-limited accounts after confirmed reset-credit redemption

When upstream confirms that a reset credit reset one or more quota windows, the forced usage refresh may recover a `RATE_LIMITED` account before its old persisted cooldown deadline. This applies only to that refresh, only when the redemption started after the block, and only when the block's deadline belongs to the quota window the refresh shows was reset; ordinary refreshes and quota-exceeded recovery keep their current rules.

## Motivation

Issue #2602 reports an account remaining blocked after upstream successfully redeemed a reset credit and returned fresh available usage. The redemption confirmation proves a window reset; binding the persisted deadline to that window (as the scheduler's #2405 recovery does) is what shows this block's cooldown no longer applies. The forced refresh still checks the new usage snapshot before clearing the status.

## Scope

Thread the redemption start time through every reset-credit refresh path (the accounts API, the dashboard API and the scheduler's automatic pre-expiry redemption) into usage recovery. Preserve the existing cooldown, usage availability checks, reset-confirmed transition logic, and five-second persisted-deadline anchor for every other path.
