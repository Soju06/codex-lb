## Context

`UsageDonuts` renders a one-column mobile grid whose `DonutChart` items retain the intrinsic minimum of a horizontal row containing a non-shrinking 152px chart, a 24px gap, and a legend. At 320x568 and 390x844, the card resolves to 475px inside a 288px or 358px content area and widens the document. The request table is intentionally wider than its local `overflow-x-auto` scroller and does not own the page overflow.

A browser-only min-width perturbation reduced the 320px document width from 491px to 324px. The remaining 4px was traced separately to the unbreakable account-section heading/summary group: its heading, gap, and `whitespace-nowrap` summary exceed the 288px content row by a few rendered pixels. It is not caused by the donut, chart overflow, or request table.

## Goals / Non-Goals

**Goals:**
- Keep both usage donut cards and their rows, fixed charts, and legends within the mobile dashboard content width at 320x568 and 390x844.
- Keep the document horizontally contained at those supported mobile viewports, including removal of the independently identified 4px account-summary residual.
- Preserve the desktop two-column donut layout and the request table's existing local horizontal scrolling.
- Prove the behavior through the rendered `/dashboard` route with deterministic, privacy-safe fixture data.

**Non-Goals:**
- Resize the 152px donut chart, change labels or data, or redesign the cards.
- Hide page overflow globally or clip a still-oversized donut card.
- Change the request table, dashboard APIs, navigation, or unrelated visual styles.
- Introduce a new responsive-layout abstraction.

## Decisions

1. **Reset intrinsic minimums at each owning donut seam.** Add the existing Tailwind `min-w-0` utility to the `UsageDonuts` grid, the `DonutChart` card, its horizontal flex row, and its flexing legend. The chart remains `shrink-0` at 152px; the legend receives the remaining width and uses its existing truncation behavior.

   Alternative: apply `overflow-x-hidden` to the page or card. Rejected because it would hide the symptom while leaving the layout wider than its container.

2. **Let the account heading and summary wrap as separate flex items on the narrowest width.** The existing section header already permits wrapping at its outer level; allowing its inner heading/summary group to wrap removes the separately measured 4px residual while preserving the summary's own one-line text and all wider layouts.

   Alternative: shorten localized copy or add another horizontal scroller. Rejected because copy width varies by locale and this compact header does not need scrolling.

3. **Test the externally failing rendered seam.** Extend the existing dashboard browser smoke with deterministic route fixtures, then assert document containment and both usage-card edges at 320x568 and 390x844. Assert separately that the request table remains wider than, and contained by, its local horizontal scroller. Include 1440x900 as the desktop negative control.

   Alternative: assert authored Tailwind classes in a component test. Rejected because class assertions cannot prove CSS grid and flex min-content behavior in a real layout engine.

## Risks / Trade-offs

- [A narrow legend has little horizontal space beside the fixed chart] → Keep the existing `truncate` label behavior and verify both labels and values remain in the contained card.
- [The account summary may move below its heading at 320px] → Preserve the summary as one line and allow only the established flex group to wrap; verify 390px and desktop remain unchanged.
- [A wide table could be mistaken for document overflow] → Assert both table/scroller width relationships and page-level containment in the same browser regression.
- [Responsive changes could alter desktop sizing] → Exercise 1440x900 with the same fixture and assert two columns plus document containment.
