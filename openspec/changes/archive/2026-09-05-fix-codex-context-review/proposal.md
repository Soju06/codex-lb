# Preserve context ownership at migration and dispatch boundaries

## Why
PR #2102 review identified adoption of pre-existing context tables, lost typed fan-out errors, premature HTTP participation, and a WebSocket sent marker set before database work completes.

## What Changes
- Reject unexpected context tables before creating either table; preserve their rows and revision on failure.
- Preserve typed partition failure statuses after cancelling and awaiting siblings.
- Bind HTTP session ownership before upstream startup and record participation after the first upstream event.
- Finish WebSocket context persistence before setting the send marker, with no await between that marker and send.

## Impact
Context routing, its unmerged migration, and regression tests. No new settings or production deployment.
