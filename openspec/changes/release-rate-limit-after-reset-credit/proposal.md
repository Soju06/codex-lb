# Release rate-limited accounts after confirmed reset-credit redemption

When upstream confirms that a reset credit reset one or more quota windows, the forced usage refresh may recover a `RATE_LIMITED` account before its old persisted cooldown deadline. This applies only to that refresh and only when the redemption completed after the block; ordinary refreshes and quota-exceeded recovery keep their current rules.

## Motivation

Issue #2602 reports an account remaining blocked after upstream successfully redeemed a reset credit and returned fresh available usage. The redemption confirmation is authoritative evidence that the old cooldown no longer applies, while the forced refresh still checks the new usage snapshot before clearing the status.

## Scope

Thread the redemption completion timestamp through the two reset-credit refresh paths into usage recovery. Preserve the existing cooldown, usage availability checks, reset-confirmed transition logic, and five-second persisted-deadline anchor for every other path.
