## Goals

Accounts list reset-credit expiry warning.

## Decisions

Port the fork warning to the existing upstream account list item. Use the existing nearest-expiry summary field and one timer for the list, with cleanup on unmount. For example, two credits expiring tomorrow gain a warning; unknown expiry does not. This is display-only and does not change redemption policy or the existing Reset action countdown.

## Validation

Exercise public UI/API behavior, edge cases, lint/type checks and strict OpenSpec validation. Capture synthetic before/after screenshots from the upstream baseline and this branch.
