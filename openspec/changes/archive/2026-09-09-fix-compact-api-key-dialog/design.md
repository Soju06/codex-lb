## Context

`ApiKeyCreateDialog` currently renders a normal grid-style `DialogContent` around a form whose General and Limits columns each own `max-h-[55vh]` scrolling. Below the `sm` breakpoint those two columns stack, so both height budgets plus the header and footer produce a 713px dialog at 320x568. Because the shared Dialog centers its content, the title and Close control move above the viewport while Create moves below it.

The existing `AutomationJobDialog` already demonstrates the project pattern for a tall dashboard form: a viewport-bounded flex-column dialog shell, fixed header and footer, and one `min-h-0` internal scroller.

## Goals / Non-Goals

**Goals:**
- Keep the create-dialog title, Close control, and Create action fully inside a 320x568 viewport.
- Make every create field reachable through one internal vertical scroll region.
- Preserve the existing stacked compact layout and two-column desktop layout, including correct rendering at 390x844.
- Preserve form state, submission, overlay/Escape dismissal, and the shared Dialog primitive.

**Non-Goals:**
- Change API key fields, payloads, validation, translations, or API behavior.
- Change `ApiKeyEditDialog` without separate evidence.
- Introduce a new dialog abstraction or modify the shared Dialog primitive.

## Decisions

1. **Use the established viewport-bounded dialog shell.** `DialogContent` will use a flex column with `max-h-[calc(100dvh-2rem)]`, `overflow-hidden`, zero outer padding, and the existing desktop width. The header receives explicit internal padding so it remains outside form scrolling. This keeps the `AutomationJobDialog` shell structure while following the account list's dynamic-viewport convention.

   Alternative: change the shared Dialog primitive. Rejected because only the API key create dialog has this proven layout failure, and global sizing changes could affect unrelated dialogs.

2. **Give the form one shrinking scroll owner.** The form will be a `flex min-h-0 flex-1 flex-col`; one body wrapper will use `min-h-0 flex-1 overflow-y-auto` and contain the existing responsive grid. The General and Limits columns will no longer set independent height or overflow constraints. This lets compact layouts scroll the stacked fields as one sequence while desktop retains two columns.

   Alternative: reduce each column's `55vh` cap on compact screens. Rejected because two independent scroll regions remain confusing, consume separate height budgets, and do not guarantee a persistent footer.

3. **Keep the action footer outside the scroller.** The existing Create action remains in `DialogFooter`, separated from the body with a top border and explicit padding. The shared absolute Close control remains anchored to the viewport-contained shell.

   Alternative: place Create inside the scrollable body. Rejected because it would remain unreachable until the operator scrolls to the end and would not satisfy the persistent-action contract.

## Risks / Trade-offs

- [Long field content could shrink the body incorrectly] → Keep `min-h-0` on both the form and its single flexing body so overflow resolves on the intended element.
- [Desktop columns could regress when their independent scroll caps are removed] → Preserve `sm:grid-cols-2` and verify the focused component plus a real desktop render.
- [Nested menus could affect dismissal] → Leave `Dialog`, `DialogContent`, and all field components unchanged; verify Escape and overlay dismissal in the browser.
