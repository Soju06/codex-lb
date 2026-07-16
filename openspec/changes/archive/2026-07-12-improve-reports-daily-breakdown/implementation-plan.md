# Reports Daily Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/reports` charts and table use the same continuous selected-range daily series, add sortable Daily Breakdown columns with default newest-first ordering, and render cached input tokens inline in the Input Tokens column.

**Architecture:** Keep the daily-row normalization inside the reports feature as a shared helper, then have the charts and table derive their rendered data from that helper. Keep sorting as local table UI state so the interaction does not affect page filters or chart rendering.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, Recharts, OpenSpec

---

### Task 1: Add failing regression tests for chart day-filling

**Files:**
- Modify: `frontend/src/features/reports/components/cost-per-day-chart.test.tsx`
- Modify: `frontend/src/features/reports/components/tokens-per-day-chart.test.tsx`
- Test: `frontend/src/features/reports/components/cost-per-day-chart.test.tsx`
- Test: `frontend/src/features/reports/components/tokens-per-day-chart.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
it("fills missing selected days with zero-value rows", () => {
  render(
    <CostPerDayChart
      startDate="2026-06-05"
      endDate="2026-06-07"
      data={[
        {
          date: "2026-06-05",
          requests: 150,
          inputTokens: 5_400_000,
          outputTokens: 59_000,
          cachedInputTokens: 0,
          costUsd: 3.77,
          activeAccounts: 2,
          errorCount: 0,
        },
        {
          date: "2026-06-07",
          requests: 179,
          inputTokens: 6_800_000,
          outputTokens: 73_000,
          cachedInputTokens: 0,
          costUsd: 4.54,
          activeAccounts: 2,
          errorCount: 0,
        },
      ]}
    />,
  );

  expect(capturedProps?.data).toEqual([
    { date: "06-05", cost: 3.77 },
    { date: "06-06", cost: 0 },
    { date: "06-07", cost: 4.54 },
  ]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bun test frontend/src/features/reports/components/cost-per-day-chart.test.tsx frontend/src/features/reports/components/tokens-per-day-chart.test.tsx`
Expected: FAIL because the chart props do not accept `startDate` / `endDate` and the missing day is not inserted.

- [ ] **Step 3: Write minimal implementation**

```ts
export type CostPerDayChartProps = {
  startDate: string;
  endDate: string;
  data: DailyReportRow[];
};

const chartData = buildContinuousDailyRows(startDate, endDate, data).map((d) => ({
  date: d.date.slice(5),
  cost: d.costUsd,
}));
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun test frontend/src/features/reports/components/cost-per-day-chart.test.tsx frontend/src/features/reports/components/tokens-per-day-chart.test.tsx`
Expected: PASS

### Task 2: Add failing regression tests for table sorting and cached-token rendering

**Files:**
- Modify: `frontend/src/features/reports/components/daily-detail-table.test.tsx`
- Test: `frontend/src/features/reports/components/daily-detail-table.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
it("sorts by day descending by default", () => {
  render(
    <DailyDetailTable
      startDate="2026-06-05"
      endDate="2026-06-07"
      data={[
        { date: "2026-06-05", requests: 1, inputTokens: 100, outputTokens: 20, cachedInputTokens: 0, costUsd: 1, activeAccounts: 1, errorCount: 0 },
        { date: "2026-06-07", requests: 3, inputTokens: 300, outputTokens: 40, cachedInputTokens: 50, costUsd: 2, activeAccounts: 2, errorCount: 0 },
      ]}
    />,
  );

  const rows = screen.getAllByTestId(/daily-breakdown-row-/);
  expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
    "daily-breakdown-row-2026-06-07",
    "daily-breakdown-row-2026-06-06",
    "daily-breakdown-row-2026-06-05",
  ]);
});

it("toggles sorting when a header is clicked", async () => {
  const user = userEvent.setup();
  render(/* table with different request counts */);
  await user.click(screen.getByRole("button", { name: /reqs/i }));
  await user.click(screen.getByRole("button", { name: /reqs/i }));
  expect(screen.getAllByTestId(/daily-breakdown-row-/)[0]).toHaveAttribute(
    "data-testid",
    "daily-breakdown-row-2026-06-07",
  );
});

it("renders cached tokens inline inside the input tokens cell", () => {
  render(/* row with inputTokens 1200000 and cachedInputTokens 960000 */);
  expect(screen.getByText("1.2M")).toBeInTheDocument();
  expect(screen.getByText("(960K)")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the table test to verify it fails**

Run: `bun test frontend/src/features/reports/components/daily-detail-table.test.tsx`
Expected: FAIL because the table renders ascending dates, has no sortable headers, and does not render cached tokens inline.

- [ ] **Step 3: Write minimal implementation**

```tsx
const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>({
  key: "date",
  direction: "desc",
});

const sortedRows = sortDailyRows(rows, sort);

<button type="button" onClick={() => toggleSort("requests")}>Reqs</button>

<td>
  <span>{formatTokens(row.inputTokens)}</span>{" "}
  <span className="text-[11px] text-muted-foreground">({formatTokens(row.cachedInputTokens)})</span>
</td>
```

- [ ] **Step 4: Run the table test to verify it passes**

Run: `bun test frontend/src/features/reports/components/daily-detail-table.test.tsx`
Expected: PASS

### Task 3: Extract the shared daily-series helper and wire the page

**Files:**
- Create: `frontend/src/features/reports/daily-series.ts`
- Modify: `frontend/src/features/reports/components/daily-detail-table.tsx`
- Modify: `frontend/src/features/reports/components/cost-per-day-chart.tsx`
- Modify: `frontend/src/features/reports/components/tokens-per-day-chart.tsx`
- Modify: `frontend/src/features/reports/components/reports-page.tsx`

- [ ] **Step 1: Write the shared helper**

```ts
export function buildContinuousDailyRows(startDate: string, endDate: string, rows: DailyReportRow[]): DailyReportRow[] {
  if (!isISODate(startDate) || !isISODate(endDate) || startDate > endDate) {
    return rows;
  }

  const rowsByDate = new Map(rows.map((row) => [row.date, row]));
  const continuousRows: DailyReportRow[] = [];

  for (let current = startDate; current <= endDate; current = nextISODate(current)) {
    continuousRows.push(rowsByDate.get(current) ?? createZeroRow(current));
  }

  return continuousRows;
}
```

- [ ] **Step 2: Update page and components to use the helper**

Run the page through one normalized data path by passing `startDate` and `endDate` to both chart components and importing the helper in the table component.

- [ ] **Step 3: Run focused reports tests**

Run: `bun test frontend/src/features/reports/components/cost-per-day-chart.test.tsx frontend/src/features/reports/components/tokens-per-day-chart.test.tsx frontend/src/features/reports/components/daily-detail-table.test.tsx`
Expected: PASS

### Task 4: Update OpenSpec task tracking and run final verification

**Files:**
- Modify: `openspec/changes/improve-reports-daily-breakdown/tasks.md`

- [ ] **Step 1: Mark completed implementation tasks in OpenSpec**

Change completed task checkboxes from `- [ ]` to `- [x]` in `openspec/changes/improve-reports-daily-breakdown/tasks.md`.

- [ ] **Step 2: Run OpenSpec validation**

Run: `openspec validate improve-reports-daily-breakdown --strict`
Expected: `Change 'improve-reports-daily-breakdown' is valid`

- [ ] **Step 3: Run final frontend verification**

Run: `bun test frontend/src/features/reports/components/cost-per-day-chart.test.tsx frontend/src/features/reports/components/tokens-per-day-chart.test.tsx frontend/src/features/reports/components/daily-detail-table.test.tsx`
Expected: PASS
