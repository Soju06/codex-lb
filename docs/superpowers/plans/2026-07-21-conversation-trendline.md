# Conversation Trendline and Count Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a distinct conversation-count sparkline to the dashboard conversation stat and remove duplicate “distinct” copy from dashboard and report cards.

**Architecture:** Keep the existing model/service-tier bucket query intact. Add a second request-log query grouped only by time bucket, count normalized non-empty conversation IDs there, and pass those rows through the existing aligned trend builder. The dashboard total remains the exact timeframe aggregate; the report totals and daily counts remain unchanged.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async repositories, Pydantic, React 19, TypeScript, Zod, Vitest, Testing Library, Bun.

## Global Constraints

- Count distinct non-empty conversation IDs; null, empty, and whitespace-only IDs MUST NOT create a bucket.
- A conversation appearing under multiple models or service tiers in one bucket MUST count once in that bucket.
- The dashboard trend series MUST use the existing timeframe bucket configuration and zero-fill missing buckets.
- The exact dashboard summary total MUST NOT be computed by summing trend points.
- Do not add dependencies, chart components, settings, feature flags, or new dashboard navigation.
- Remove the “`{count} distinct`” secondary copy from both dashboard and report conversation cards.
- Format the filtered conversation summary as `The conversation [ID] runs [count] request(s), cost = [cost]` with styled inline-code values and no literal backticks.
- Do not commit changes unless the user explicitly requests a commit.

---

### Task 1: Add a correct bucket-only conversation aggregate

**Files:**
- Modify: `app/core/usage/types.py:114-126`
- Modify: `app/modules/request_logs/repository.py:171-219`
- Modify: `app/modules/dashboard/repository.py:8-67`
- Test: `tests/unit/test_request_logs_repository.py`

**Interfaces:**
- Consumes: persisted nullable `RequestLog.conversation_id`, the existing
  `_conversation_id_expr()` normalization, and the existing warmup exclusion.
- Produces: `BucketConversationAggregate(bucket_epoch: int,
  conversation_count: int)` and
  `RequestLogsRepository.aggregate_conversations_by_bucket(since:
  datetime, bucket_seconds: int = 21600) ->
  list[BucketConversationAggregate]`, exposed as
  `DashboardRepository.aggregate_conversations_by_bucket(...)`.

- [ ] **Step 1: Write the failing repository test**

Add an async test to `tests/unit/test_request_logs_repository.py` that inserts
five logs at the same bucket boundary: two `conv-a` rows using different models,
one `conv-b` row, one whitespace-only ID, and one warmup row with `conv-warmup`.
Insert another `conv-a` row in the next bucket. Query from the first bucket and
assert the result is two rows with counts `[2, 1]`; the first bucket must count
`conv-a` once despite its two models, and the warmup/blank IDs must not count.

The assertion should use the public result object rather than inspecting SQL:

```python
rows = await repo.aggregate_conversations_by_bucket(SINCE, bucket_seconds=3600)

assert [(row.bucket_epoch, row.conversation_count) for row in rows] == [
    (int(SINCE.timestamp()), 2),
    (int((SINCE + timedelta(hours=1)).timestamp()), 1),
]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_request_logs_repository.py -k conversation_bucket -v
```

Expected: FAIL because `aggregate_conversations_by_bucket` and
`BucketConversationAggregate` do not exist.

- [ ] **Step 3: Add the aggregate type and repository query**

Add the frozen dataclass next to `BucketModelAggregate`:

```python
@dataclass(frozen=True)
class BucketConversationAggregate:
    bucket_epoch: int
    conversation_count: int
```

Refactor the database-specific bucket-expression construction used by
`aggregate_by_bucket()` into one private repository helper, then use it in the
new query. The new query must select only the bucket expression and
`func.count(func.distinct(self._conversation_id_expr()))`, apply the requested
time boundary and `_exclude_warmup_clause()`, group/order by the bucket, and map
rows to `BucketConversationAggregate`.

Do not add `conversation_count` to `BucketModelAggregate`; its model/service-tier
grouping cannot represent distinct conversations safely.

- [ ] **Step 4: Expose the query through `DashboardRepository`**

Import `BucketConversationAggregate` and add this thin delegation beside
`aggregate_logs_by_bucket()`:

```python
async def aggregate_conversations_by_bucket(
    self,
    since: datetime,
    bucket_seconds: int = 21600,
) -> list[BucketConversationAggregate]:
    return await self._logs_repo.aggregate_conversations_by_bucket(since, bucket_seconds)
```

- [ ] **Step 5: Run the repository tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_request_logs_repository.py -k conversation_bucket -v
```

Expected: PASS, including the cross-model de-duplication and warmup/blank-ID
assertions.

---

### Task 2: Produce and serve the conversation trend series

**Files:**
- Modify: `app/modules/usage/schemas.py:57-66`
- Modify: `app/modules/usage/builders.py:7-166`
- Modify: `app/modules/dashboard/service.py:94-108`
- Test: `tests/unit/test_dashboard_trends.py`
- Test: `tests/integration/test_dashboard_overview.py`

**Interfaces:**
- Consumes: `list[BucketConversationAggregate]` from Task 1 and the existing
  `bucket_since`, `bucket_seconds`, and `bucket_count` timeframe values.
- Produces: `MetricsTrends.conversations: list[TrendPoint]` with one point per
  configured slot and a dashboard JSON field `trends.conversations`.

- [ ] **Step 1: Write the failing builder test**

Import `BucketConversationAggregate` and add a test in
`tests/unit/test_dashboard_trends.py` that passes two conversation rows to
`build_trends_from_buckets`: slot 2 has count 3 and slot 5 has count 1. Assert
the two values land in those slots and every other conversation point is zero.
Also extend the empty-row test to assert 28 zero conversation points.

Use the intended builder shape:

```python
conversation_rows = [
    BucketConversationAggregate(bucket_epoch=FIRST_SLOT_EPOCH + 2 * BUCKET_SECONDS, conversation_count=3),
    BucketConversationAggregate(bucket_epoch=FIRST_SLOT_EPOCH + 5 * BUCKET_SECONDS, conversation_count=1),
]
trends, _, _ = build_trends_from_buckets([], SINCE, conversation_rows=conversation_rows)

assert trends.conversations[2].v == 3
assert trends.conversations[5].v == 1
assert sum(point.v for point in trends.conversations) == 4
```

- [ ] **Step 2: Run the builder test and confirm it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_dashboard_trends.py -k conversation -v
```

Expected: FAIL because `MetricsTrends` has no conversation series and the
builder has no `conversation_rows` parameter.

- [ ] **Step 3: Extend the schema and builder with aligned zero-filled points**

Add `conversations` to `MetricsTrends`:

```python
class MetricsTrends(DashboardModel):
    requests: list[TrendPoint] = Field(default_factory=list)
    tokens: list[TrendPoint] = Field(default_factory=list)
    cost: list[TrendPoint] = Field(default_factory=list)
    error_rate: list[TrendPoint] = Field(default_factory=list)
    conversations: list[TrendPoint] = Field(default_factory=list)
```

Extend `build_trends_from_buckets()` with
`conversation_rows: list[BucketConversationAggregate] | None = None`. Build a
`bucket_conversations` dictionary only from rows whose epoch is in `slot_set`,
append one `TrendPoint` per slot, and pass the resulting list into
`MetricsTrends`. Keep request/token/cost/error totals exactly as they are.

- [ ] **Step 4: Fetch conversation buckets in `DashboardService`**

After the existing `bucket_rows` query, call the Task 1 delegation with the
same `bucket_query_since` and `overview_timeframe.bucket_seconds`:

```python
conversation_bucket_rows = await self._repo.aggregate_conversation_logs_by_bucket(
    bucket_query_since,
    overview_timeframe.bucket_seconds,
)
```

Pass `conversation_rows=conversation_bucket_rows` to
`build_trends_from_buckets()`. Keep the calls sequential because both queries
use the same async SQLAlchemy session.

- [ ] **Step 5: Run the builder tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_dashboard_trends.py -v
```

Expected: PASS with existing series behavior unchanged and the new conversation
series zero-filled.

- [ ] **Step 6: Add dashboard API regression assertions**

In `tests/integration/test_dashboard_overview.py`, extend the existing trend
length assertions with `len(trends["conversations"]) == expected_bucket_count`.
In the distinct-conversation timeframe test, assert the conversation trend has
the expected non-zero count in the populated slot and that the summary total
still counts repeated `conv-a` only once. Add a second model for one repeated
conversation if the fixture does not already exercise cross-model grouping.

- [ ] **Step 7: Run the focused dashboard backend suite**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_dashboard_trends.py tests/integration/test_dashboard_overview.py -v
```

Expected: PASS, including 1d/7d/30d bucket lengths covered by the existing
overview tests.

---

### Task 3: Consume the trendline in dashboard frontend state

**Files:**
- Modify: `frontend/src/features/dashboard/schemas.ts:73-83`
- Modify: `frontend/src/features/dashboard/utils.ts:820-829`
- Modify: `frontend/src/features/dashboard/components/dashboard-page.tsx:1-250,406-409`
- Modify: `frontend/src/test/mocks/factories.ts:302-307`
- Test: `frontend/src/features/dashboard/utils.test.ts`
- Test: `frontend/src/features/dashboard/schemas.test.ts`
- Test: `frontend/src/features/dashboard/hooks/use-dashboard.test.ts`
- Test: `frontend/src/features/dashboard/components/dashboard-page.test.tsx`

**Interfaces:**
- Consumes: backend `trends.conversations` from Task 2.
- Produces: a `DashboardStat` whose `trend` contains the conversation bucket
  values and whose `meta` is absent, plus a formatted filtered-conversation
  summary.

- [ ] **Step 1: Add a failing frontend assertion**

Update the dashboard utility test that builds the Conversations stat to supply
non-zero conversation trend points through `createDashboardOverview()`, then
assert:

```typescript
const conversationStat = viewWithoutBurn.stats.find((stat) => stat.label.includes("Conversations"));
expect(conversationStat?.trend).toEqual([{ value: 2 }, { value: 0 }, { value: 4 }]);
expect(conversationStat?.meta).toBeUndefined();
```

Use a short deterministic trend fixture in `createDashboardOverview()` so the
test does not depend on the default request/token values.

- [ ] **Step 2: Run the focused frontend test and confirm it fails**

Run:

```bash
bun run --cwd frontend test -- src/features/dashboard/utils.test.ts
```

Expected: FAIL because the mock trend payload has no conversation series and
the utility currently emits an empty trend plus distinct metadata.

- [ ] **Step 3: Extend the Zod trend schema and fixtures**

Add the conversation series to `MetricsTrendsSchema`. Use a default empty array
for frontend compatibility with older cached/mock payloads while the backend
always emits the field:

```typescript
const MetricsTrendsSchema = z.object({
  requests: z.array(TrendPointSchema),
  tokens: z.array(TrendPointSchema),
  cost: z.array(TrendPointSchema),
  errorRate: z.array(TrendPointSchema),
  conversations: z.array(TrendPointSchema).default([]),
});
```

Add `conversations` to `createDashboardOverview()` and to any explicit
`EMPTY_TRENDS` fixtures in dashboard schema/hook tests. Add one schema test
that parses a conversation trend point and preserves its value.

- [ ] **Step 4: Wire the stat and remove dashboard copy**

Change the Conversations stat in `frontend/src/features/dashboard/utils.ts` to
use the existing conversion helper and omit `meta`:

```typescript
stats.push({
  label: t("dashboard.stats.conversations", { timeframe: timeframeLabel }),
  value: formatNumber(conversationCount),
  icon: MessageSquare,
  trend: trendPointsToValues(trends.conversations),
  trendColor: TREND_COLORS[3],
});
```

- [ ] **Step 5: Run dashboard frontend tests and typecheck**

Run:

```bash
bun run --cwd frontend test -- src/features/dashboard/utils.test.ts src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-dashboard.test.ts
bun run --cwd frontend typecheck
```

Expected: PASS with no TypeScript errors and no dashboard stat metadata reading
“distinct”.

- [ ] **Step 6: Format the filtered conversation summary**

Import `Trans` from `react-i18next` and change the `conversationSummary` memo in
`dashboard-page.tsx` to return the selected translation key plus interpolation
values instead of a fully interpolated string. Render it with three existing
`Trans` component slots, each using a semantic `code` element:

```tsx
<Trans
  i18nKey={conversationSummary.key}
  values={conversationSummary.values}
  components={[
    <code key="conversation-id" className="rounded bg-muted px-1 font-mono text-xs text-foreground" />,
    <code key="request-count" className="rounded bg-muted px-1 font-mono text-xs tabular-nums text-foreground" />,
    <code key="cost" className="rounded bg-muted px-1 font-mono text-xs tabular-nums text-foreground" />,
  ]}
/>
```

Update the English, Korean, and Simplified Chinese summary translations to use
the matching `<0>`, `<1>`, and `<2>` tags and the new punctuation:

```json
"dashboard.conversation.summary": "The conversation <0>{{id}}</0> runs <1>{{count}}</1> request(s), cost = <2>{{cost}}</2>",
"dashboard.conversation.summaryWithFilters": "The conversation <0>{{id}}</0> runs <1>{{count}}</1> request(s), cost = <2>{{cost}}</2> — filters: {{filters}}"
```

Do not emit literal backticks. Add `data-testid="conversation-summary"` to the
summary paragraph and assert in `dashboard-page.test.tsx` that the ID, count,
and cost are present as `code` elements, while the rendered text contains
`cost = $3.14` and not `for $3.14`.

- [ ] **Step 7: Run the expanded dashboard frontend checks**

Run:

```bash
bun run --cwd frontend test -- src/features/dashboard/utils.test.ts src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-dashboard.test.ts src/features/dashboard/components/dashboard-page.test.tsx
bun run --cwd frontend typecheck
bun run --cwd frontend lint
```

Expected: PASS with styled values in the summary and no literal backticks.

---

### Task 4: Remove duplicate report copy without changing report data

**Files:**
- Modify: `frontend/src/features/reports/components/reports-summary-cards.tsx:19-84`
- Modify: `frontend/src/i18n/locales/en.json:706,931`
- Modify: `frontend/src/i18n/locales/ko.json` (matching keys)
- Modify: `frontend/src/i18n/locales/zh-CN.json` (matching keys)
- Test: `frontend/src/features/reports/components/reports-summary-cards.test.tsx`

**Interfaces:**
- Consumes: existing `summary.totalConversations` report value.
- Produces: a Conversations card with the numeric value only; existing summary
  cards keep their current secondary text.

- [ ] **Step 1: Write the failing report-copy assertion**

In the existing Conversations card test, retain the assertions for the card,
label, and `42` value, then replace the positive copy assertion with:

```typescript
expect(within(conversationsCard).queryByText("42 distinct")).not.toBeInTheDocument();
```

- [ ] **Step 2: Run the report test and confirm it fails**

Run:

```bash
bun run --cwd frontend test -- src/features/reports/components/reports-summary-cards.test.tsx
```

Expected: FAIL because the report card currently renders `42 distinct`.

- [ ] **Step 3: Remove only the conversation secondary copy**

Omit the `sub` property from the conversations card. Render the secondary row
only when a card has secondary content so the conversation card does not leave
an empty text block:

```tsx
{card.sub ? <div className="mt-0.5 text-xs text-muted-foreground">{card.sub}</div> : null}
```

Remove only `dashboard.stats.distinctConversations` and
`reports.summary.conversationsSub` from all three locale JSON files. Confirm
with a repository search that no source or test still references either key.

- [ ] **Step 4: Run report tests, typecheck, and lint**

Run:

```bash
bun run --cwd frontend test -- src/features/reports/components/reports-summary-cards.test.tsx src/features/reports/schemas.test.ts src/features/reports/components/daily-detail-table.test.tsx
bun run --cwd frontend typecheck
bun run --cwd frontend lint
```

Expected: PASS; report daily counts, sorting, and export tests remain unchanged.

---

### Task 5: Synchronize OpenSpec and run final focused verification

**Files:**
- Modify: `openspec/changes/support-conversation-grouping/design.md`
- Modify: `openspec/changes/support-conversation-grouping/tasks.md`
- Modify: `openspec/changes/support-conversation-grouping/specs/frontend-architecture/spec.md`
- Modify: `openspec/changes/support-conversation-grouping/specs/query-caching/spec.md`

**Interfaces:**
- Consumes: completed backend and frontend behavior from Tasks 1–4.
- Produces: normative OpenSpec coverage for bucket-level distinct conversation
  trends and the no-duplicate-copy presentation rule.

- [ ] **Step 1: Add normative OpenSpec requirements**

Add a dashboard trend requirement to the active frontend architecture delta:

```markdown
### Requirement: Dashboard conversation trends are bucketed distinctly

The dashboard overview MUST expose `trends.conversations` with one point for
each configured timeframe bucket. Each point MUST count distinct, non-empty
conversation IDs within that bucket, and a conversation repeated across models
or service tiers in one bucket MUST count once. Missing buckets MUST be zero.
The dashboard Conversations card MUST use this series and MUST NOT render a
`{count} distinct` secondary label; the report Conversations card MUST also
omit that secondary label. The filtered conversation summary MUST render the
conversation ID, request count, and cost as styled inline-code values using the
copy `The conversation [ID] runs [count] request(s), cost = [cost]`; literal
backtick characters MUST NOT be rendered.
```

Add scenarios for cross-model de-duplication, zero-filled buckets, and the
absence of the duplicate copy. Add the query-level distinct bucket requirement
to the query-caching delta, then add an unchecked follow-up task in
`tasks.md` for this trendline/copy correction and mark it complete only after
verification.

- [ ] **Step 2: Validate the OpenSpec artifacts**

Run:

```bash
openspec validate --specs
```

Expected: PASS with no malformed requirements or missing scenario headers.

- [ ] **Step 3: Run all focused backend and frontend checks**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_request_logs_repository.py -k conversation_bucket -v
.venv/bin/python -m pytest tests/unit/test_dashboard_trends.py tests/integration/test_dashboard_overview.py -v
bun run --cwd frontend test -- src/features/dashboard/utils.test.ts src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-dashboard.test.ts src/features/dashboard/components/dashboard-page.test.tsx src/features/reports/components/reports-summary-cards.test.tsx src/features/reports/schemas.test.ts src/features/reports/components/daily-detail-table.test.tsx
bun run --cwd frontend typecheck
bun run --cwd frontend lint
```

Expected: every command exits successfully.

- [ ] **Step 4: Check the final diff and working state**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Confirm only the trendline implementation, count-copy cleanup, focused tests,
OpenSpec artifacts, and the two Superpowers planning documents are changed.
Do not archive the OpenSpec change or claim completion while any unchecked task
or failing verification remains.
