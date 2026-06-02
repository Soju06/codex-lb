## 1. DonutChart Component

- [x] 1.1 Add `centerLayout?: "remaining" | "credits"` prop (default `"remaining"`).
- [x] 1.2 When `centerLayout === "credits"`, render `Credits` caption + `{remaining}/{total}` formatted with `formatNumber`.
- [x] 1.3 Preserve the existing `"remaining"` branch behavior unchanged for backwards compatibility.

## 2. Dashboard Usage Donuts

- [x] 2.1 Pass `centerLayout="credits"` for both donuts.
- [x] 2.2 Rename titles to `"Hourly Credits"` and `"Weekly Credits"`.

## 3. Tests

- [x] 3.1 Update `usage-donuts.test.tsx` to assert the new titles.
- [x] 3.2 Add a regression test for the issue-reported fraction (`7331/7560`).
- [x] 3.3 Verify `donut-chart.test.tsx` continues to pass under the default `"remaining"` layout.

## 4. Spec Delta

- [x] 4.1 Add `openspec/changes/show-credits-as-fraction/specs/frontend-architecture/spec.md` describing the dashboard donut presentation.
- [x] 4.2 `openspec validate --specs` (if available).
