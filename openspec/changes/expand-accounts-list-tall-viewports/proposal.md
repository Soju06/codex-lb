# Change: Expand the accounts list on tall viewports

## Why

The Accounts page stretches its left card to match the selected-account detail
column, but the rows region is capped at 32rem. On tall windows this leaves a
large blank area inside the card while additional accounts remain hidden behind
a short nested scrollbar.

## What Changes

- Remove the fixed 32rem ceiling from the account rows region while bounding the
  complete list column so optional controls use their rendered height and the
  remaining rows stay above the fixed status bar.
- Stop stretching the left card to the selected-account detail height when its
  controls and account rows need less space.
- Add tall-viewport browser coverage proving that the rows region grows beyond
  32rem, still scrolls when necessary, and leaves no artificial gap below it.

## Impact

- Affected spec: `frontend-architecture`
- Affected code: Accounts list layout and frontend layout regressions
- Affected users: Operators viewing long account lists on tall displays
