## Context

Accounts currently uses a compact list and selected detail. Stored ID tokens include `https://api.openai.com/auth.chatgpt_subscription_active_until` and `chatgpt_subscription_last_checked`; a read-only metadata audit found an explicit end date for 129 of 131 Plus accounts. The current auth parser ignores these fields. The deployment contains over 3,000 accounts, making unbounded rich-card rendering unsuitable.

## Goals / Non-Goals

**Goals:** Display recorded subscription term separately from credentials and quota; provide a responsive overview grid with existing filters and management access.

**Non-Goals:** Calling undocumented billing endpoints, predicting renewal, altering routing or subscription status, persisting duplicate deadlines, or deploying this change.

## Decisions

- Derive nullable `subscription.activeUntil` and `subscription.lastCheckedAt` from the stored ID token in the account summary mapper. Parse optional timestamps independently so malformed subscription metadata cannot invalidate identity claims. Accept ISO datetimes with a timezone; normalize upstream naive ISO timestamps to UTC. Never infer subscription time from JWT `exp`, quota reset, or import time. Suppress historical paid-term data for currently free/unknown accounts or a mismatched token plan.
- Reuse existing list items and usage/token components in grid cards. Keep list as default and remember the Accounts-specific preference in local storage. Share search, status filters and sort. Render at most 24 grid cards per page; open existing account details in a dialog on selection, with no per-card network calls.
- Subscription UI shows remaining time and the recorded date, using an elapsed-record state instead of claiming the account has definitively expired. Show the last-checked timestamp and explain that renewal may change the period. Unknown remains unknown. Refresh countdowns on a single page-level minute tick.

## Risks / Trade-offs

- Stored token data is a snapshot and may predate renewal → label it as a recorded term; retain normal account refresh behavior and never change account eligibility from this field.
- Optional metadata is malformed → independently return null and preserve account identity/list responses.
- Rich grids amplify rendering costs → client-side pagination and details fetched only for an opened account.
- Local storage unavailable → switching views still works for the current page session.

## Example

With an active-until date of `2026-10-10T00:00:00Z`, the card on September 27 displays the remaining period and October 10. A token whose `exp` is September 28 but lacks subscription metadata displays unknown subscription time, while token expiry remains September 28.
