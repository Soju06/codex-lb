## Why

The Accounts page does not adapt cleanly on narrow viewports, which makes the
list/detail layout and dense account controls hard to use on mobile-sized
screens.

The API key edit dialog also closes when an operator chooses items from
portalled select menus and then clicks away to save, because the dialog treats
select-menu interaction as an outside click.

## What Changes

- Tighten Accounts page responsive layout constraints so the list and detail
  panels fit mobile and tablet widths without horizontal overflow.
- Keep account list controls, quota rows, detail sections, and action controls
  usable at narrow widths.
- Prevent API key edit dialog dismissal for interactions inside portalled
  select/popover/calendar menu surfaces while preserving normal outside-click
  dismissal.
- Add focused frontend regression coverage for both issues.

## Impact

Operators can manage accounts and edit API keys reliably across desktop and
mobile-sized dashboard viewports.
