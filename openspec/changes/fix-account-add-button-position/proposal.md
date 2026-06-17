## Why

Operators with enough accounts can lose the Accounts page add-account action at
the bottom of the scrollable account list. Adding another account should not
require scrolling through every existing account first.

## What Changes

- Move the add-account trigger out of the scrollable account list and into the
  persistent list controls.
- Keep the existing add-account chooser and OAuth/import flows unchanged.
- Add regression coverage that the trigger is outside the scroll region.

## Impact

Operators can always reach the add-account action from the Accounts page list
controls, even when the account pool is long.
