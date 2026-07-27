# Conversation Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist harness conversation IDs for HTTP and WebSocket requests and
use them for request-log filtering, cost aggregation, dashboard statistics, and
report statistics.

**Architecture:** Extend the existing request-log metadata flow with one
ordered, case-insensitive harness rule table. Store a nullable indexed
`conversation_id`, compose it with existing request-log filters, and add
distinct counts to existing dashboard/report aggregate queries. The frontend
uses the existing URL-backed filter state and current dashboard/report
components; no new framework or dependency is needed.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, Pydantic, pytest,
React 19, TypeScript, Zod, TanStack Query, Vitest, Tailwind CSS, i18next.

## Global Constraints

- Treat `openspec/changes/support-conversation-grouping/design.md` as the
  approved behavior contract.
- Match trimmed user-agent prefixes and header names case-insensitively; trim
  only surrounding conversation-ID whitespace.
- OpenCode header precedence is `x-parent-session-id`, `x-opencode-session`,
  `x-session-id`, then `x-session-affinity`; Codex uses `thread-id`.
- Unsupported harnesses and missing/empty configured headers persist null.
- Count distinct non-empty IDs; do not create an unknown bucket or scope an ID
  by harness.
- Conversation summaries compose with every active request-log filter and are
  independent of pagination.
- Add no dependency, feature flag, backfill, README section, or changelog edit.
- Do not commit unless the user explicitly authorizes commits.
- UI write ownership belongs to a designer agent; later mechanical fixes must
  preserve its layout, spacing, hierarchy, responsive behavior, and copy
  structure.

---

### Task 1: Complete the OpenSpec change artifacts

**Files:**
- Create: `openspec/changes/support-conversation-grouping/.openspec.yaml`
- Create: `openspec/changes/support-conversation-grouping/proposal.md`
- Create: `openspec/changes/support-conversation-grouping/specs/proxy-runtime-observability/spec.md`
- Create: `openspec/changes/support-conversation-grouping/specs/query-caching/spec.md`
- Create: `openspec/changes/support-conversation-grouping/specs/frontend-architecture/spec.md`
- Verify: `openspec/changes/support-conversation-grouping/design.md`
- Track: `openspec/changes/support-conversation-grouping/tasks.md`

**Interfaces:**
- Consumes: Approved decisions in `design.md`.
- Produces: A valid `spec-driven` OpenSpec change governing all later tasks.

- [x] **Step 1: Add change metadata and proposal**

  Write `.openspec.yaml` with `schema: spec-driven` and
  `created: 2026-07-20`. Write `proposal.md` with Why, What Changes,
  Capabilities, and Impact sections. Modified capabilities are exactly
  `proxy-runtime-observability`, `query-caching`, and
  `frontend-architecture`; `responses-api-compat` is unaffected because the
  feature does not change routing or wire compatibility.

- [x] **Step 2: Add normative proxy observability requirements**

  Add requirements and WHEN/THEN scenarios proving Codex `thread-id`, OpenCode
  ordered fallback, case-insensitive prefix/header matching, unsupported
  harness null behavior, nullable indexed persistence, and propagation through
  HTTP, WebSocket, preflight-error, compact, control, transcription, file,
  warmup, thread-goal, and model-source log paths.

- [x] **Step 3: Add normative request-log query requirements**

  Specify `conversation_id` filtering, composition with existing filters,
  pagination-independent `conversation.requestCount` and
  `conversation.aggregatedCostUsd`, null response metadata without the filter,
  zero aggregates for no matches, and conversation ID participation in the
  listing-count cache signature.

- [x] **Step 4: Add normative frontend/reporting requirements**

  Specify the detail-dialog field, click behavior, badge placement/dismissal,
  summary box and inline filter suffix, dashboard card ordering, report summary
  card ordering, daily distinct-count semantics, column ordering, sorting, and
  CSV export.

- [x] **Step 5: Validate the change before implementation**

  Run: `openspec validate --specs`

  Expected: exit code 0 with no invalid requirement or scenario errors.

---

### Task 2: Add conversation detection and database storage

**Files:**
- Modify: `app/modules/proxy/_service/support.py:444-453`
- Modify: `app/db/models.py:240-329`
- Modify: `app/modules/request_logs/repository.py:367-501`
- Create: `app/db/alembic/versions/20260720_000000_add_request_log_conversation_id.py`
- Test: `tests/unit/test_proxy_utils.py:1258-1300`
- Test: `tests/unit/test_request_logs_repository.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `Mapping[str, str]` inbound headers.
- Produces: `_request_log_client_fields(headers) -> tuple[str | None, str | None, str | None]`
  returning full user-agent, user-agent group, and conversation ID;
  `RequestLog.conversation_id`; and
  `RequestLogsRepository.add_log(..., conversation_id: str | None = None)`.

- [x] **Step 1: Write failing metadata-detection tests**

  Extend `tests/unit/test_proxy_utils.py` with table-driven assertions for:

  ```python
  @pytest.mark.parametrize(
      ("headers", "expected"),
      [
          ({"User-Agent": "codex/1.2", "thread-id": " conv-a "}, "conv-a"),
          ({"user-agent": "CODEX/1.2", "Thread-Id": "conv-b"}, "conv-b"),
          ({"User-Agent": "opencode/1.0", "x-parent-session-id": "parent", "x-opencode-session": "child", "x-session-id": "fallback", "x-session-affinity": "affinity"}, "parent"),
          ({"User-Agent": "opencode/1.0", "x-opencode-session": "primary", "x-session-id": "fallback"}, "primary"),
          ({"User-Agent": "opencode/1.0", "x-parent-session-id": " ", "x-opencode-session": " ", "X-Session-Id": "fallback"}, "fallback"),
          ({"User-Agent": "opencode/1.0", "x-session-affinity": "affinity"}, "affinity"),
          ({"User-Agent": "other/1.0", "thread-id": "ignored"}, None),
          ({"thread-id": "ignored"}, None),
      ],
  )
  def test_request_log_client_fields_detect_conversation(headers, expected):
      assert proxy_service._request_log_client_fields(headers)[2] == expected
  ```

- [x] **Step 2: Run the focused helper tests and confirm failure**

  Run: `.venv/bin/python -m pytest tests/unit/test_proxy_utils.py -k "request_log_client_fields" -v`

  Expected: FAIL because `_request_log_client_fields` does not exist.

- [x] **Step 3: Implement the minimal ordered rule helper**

  In `support.py`, retain `_request_log_useragent_fields` and add:

  ```python
  _CONVERSATION_HEADERS_BY_USERAGENT_PREFIX = (
      ("opencode", ("x-parent-session-id", "x-opencode-session", "x-session-id", "x-session-affinity")),
      ("codex", ("thread-id",)),
  )


  def _request_log_client_fields(
      headers: Mapping[str, str],
  ) -> tuple[str | None, str | None, str | None]:
      useragent, useragent_group = _request_log_useragent_fields(headers)
      normalized_useragent = (useragent or "").strip().casefold()
      normalized_headers = {key.casefold(): value for key, value in headers.items()}
      for prefix, header_names in _CONVERSATION_HEADERS_BY_USERAGENT_PREFIX:
          if normalized_useragent.startswith(prefix):
              for header_name in header_names:
                  value = normalized_headers.get(header_name)
                  if value and (conversation_id := value.strip()):
                      return useragent, useragent_group, conversation_id
              break
      return useragent, useragent_group, None
  ```

- [x] **Step 4: Run the helper tests and confirm success**

  Run: `.venv/bin/python -m pytest tests/unit/test_proxy_utils.py -k "request_log_client_fields or request_log_useragent_fields" -v`

  Expected: PASS.

- [x] **Step 5: Write failing repository and migration assertions**

  Add a repository test that calls `add_log(conversation_id=" conv-a ", ...)`
  and asserts the stored value is `conv-a`, plus an empty-value case asserting
  null. Extend migration coverage to assert the column and
  `idx_logs_conversation_id` exist after upgrade and are removed by downgrade.

- [x] **Step 6: Add the ORM field, index, migration, and repository argument**

  Add after `useragent_group`:

  ```python
  conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
  ```

  Add `Index("idx_logs_conversation_id", "conversation_id")`. The migration
  must revise `20260717_000000_optimize_dashboard_hot_path_indexes`, add the
  nullable `String` column before creating the index, and drop the index before
  the column on downgrade. In `add_log`, normalize with
  `(conversation_id or "").strip() or None` and assign it to `RequestLog`.

- [x] **Step 7: Verify storage and migration behavior**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/unit/test_request_logs_repository.py -v
  .venv/bin/python -m pytest tests/integration/test_migrations.py -v
  ```

  Expected: PASS.

---

### Task 3: Propagate conversation IDs through HTTP and WebSocket logging

**Files:**
- Modify: `app/modules/proxy/_service/request_log.py:145-507`
- Modify: `app/modules/proxy/_service/support.py:632-817`
- Modify: `app/modules/proxy/_service/streaming/retry.py:244`
- Modify: `app/modules/proxy/_service/streaming/mixin.py:437-1092`
- Modify: `app/modules/proxy/_service/websocket/mixin.py:754-4783`
- Modify: `app/modules/proxy/_service/http_bridge/request_submit.py:280`
- Modify: `app/modules/proxy/_service/compact.py:579`
- Modify: `app/modules/proxy/_service/codex_control.py:161`
- Modify: `app/modules/proxy/_service/transcribe.py:170`
- Modify: `app/modules/proxy/_service/file_ops.py:420`
- Modify: `app/modules/proxy/_service/warmup.py:306`
- Modify: `app/modules/proxy/service.py:993`
- Modify: `app/modules/proxy/api.py:5907-5933`
- Test: `tests/unit/test_proxy_utils.py`
- Test: `tests/integration/test_proxy_websocket_responses.py`

**Interfaces:**
- Consumes: `_request_log_client_fields(headers)` and repository storage from
  Task 2.
- Produces: `conversation_id: str | None` on `_write_request_log`,
  `_persist_request_log`, `_write_stream_preflight_error`, and
  `_WebSocketRequestState`.

- [x] **Step 1: Write failing HTTP and WebSocket propagation tests**

  Extend existing proxy tests that capture or inspect persisted request-log
  arguments. Keep behavioral coverage for one normal Codex HTTP request with
  `thread-id`, one OpenCode HTTP preflight-error request using a fallback
  header, and one WebSocket response-create request with `x-opencode-session`;
  assert each captured/persisted `conversation_id` equals the inbound value.
  Add one behavioral request-log assertion for each auxiliary path: compact,
  control, transcription, file, warmup, thread-goal, and model-source. Each
  assertion MUST exercise that path with a supported conversation header and
  verify the captured or persisted `conversation_id`, rather than relying on
  source inspection or grep. Add an unsupported-user-agent assertion for null.
  The model-source assertion MUST also verify that its existing
  `useragent_group` value is unchanged.

- [x] **Step 2: Run the focused proxy tests and confirm failure**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/unit/test_proxy_utils.py -k "conversation" -v
  .venv/bin/python -m pytest tests/integration/test_proxy_websocket_responses.py -k "conversation" -v
  ```

  Expected: FAIL because logging contracts do not carry `conversation_id`.

- [x] **Step 3: Extend the shared logging signatures**

  Add `conversation_id: str | None = None` to `_write_request_log`,
  `_persist_request_log`, and `_write_stream_preflight_error`; forward it to
  `RequestLogsRepository.add_log`. Add the nullable field to
  `_WebSocketRequestState`.

- [x] **Step 4: Replace metadata derivation at every existing call site**

  Replace each two-value `_request_log_useragent_fields(headers)` assignment
  found under `app/modules/proxy`, except the model-source path's intentional
  preservation of its existing `useragent_group` behavior, with:

  ```python
  useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
  ```

  Store/pass all three values together through streaming retries, preflight
  errors, HTTP bridge request state, WebSocket request state/finalization, and
  each non-stream endpoint. The model-source path must detect and store
  `conversation_id` using the shared rule table while retaining its current
  `useragent_group` derivation and value. Do not introduce a model-source
  `useragent_group` behavior change.

- [x] **Step 5: Prove no old derivation call site remains outside the helper**

  Run:

  ```bash
  rg "_request_log_useragent_fields\(" app/modules/proxy
  rg "conversation_id" app/modules/proxy/_service app/modules/proxy/service.py app/modules/proxy/api.py
  ```

  Expected: the first command reports the helper definition/use plus only the
  intentionally retained model-source useragent-group derivation; the second
  covers every request-log path listed in this task. The behavioral test matrix
  from Step 1 is required evidence in addition to these structural checks.

- [x] **Step 6: Run focused HTTP/WS regression tests**

  Run the two commands from Step 2 again.

  Expected: PASS.

---

### Task 4: Add request-log API filtering and conversation aggregation

**Files:**
- Modify: `app/modules/request_logs/repository.py:538-854`
- Modify: `app/modules/request_logs/service.py:45-103`
- Modify: `app/modules/request_logs/schemas.py:17-65`
- Modify: `app/modules/request_logs/mappers.py:1-78`
- Modify: `app/modules/request_logs/api.py:40-76`
- Test: `tests/integration/test_request_logs_api.py`
- Test: `tests/integration/test_request_logs_filters.py`
- Test: `tests/integration/test_request_logs_list_count.py`

**Interfaces:**
- Consumes: `RequestLog.conversation_id` from Task 2.
- Produces: `GET /api/request-logs?conversation_id=...`, row-level
  `conversationId`, and nullable response-level
  `conversation: { requestCount, aggregatedCostUsd }`.

- [x] **Step 1: Write failing API contract tests**

  Seed two rows sharing `conv-a`, one `conv-b` row, and rows that differ by
  status/model/time. Assert that `conversation_id=conv-a` returns only matching
  rows, composes with status/model/time filters, ignores pagination for
  aggregate count/cost, returns zero aggregates for no matches, and returns
  `conversation: null` without the filter. Assert the response-level
  conversation metadata contains only `requestCount` and
  `aggregatedCostUsd`, never a duplicated ID. Assert different conversation
  IDs do not share cached totals.

- [x] **Step 2: Run request-log API tests and confirm failure**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/integration/test_request_logs_api.py tests/integration/test_request_logs_filters.py tests/integration/test_request_logs_list_count.py -k "conversation" -v
  ```

  Expected: FAIL because the query parameter and response fields are absent.

- [x] **Step 3: Extend schemas and mapping**

  Add `conversation_id: str | None = None` to `RequestLogEntry` and map it from
  the ORM row. Add:

  ```python
  class RequestLogConversation(DashboardModel):
      request_count: int
      aggregated_cost_usd: float


  class RequestLogsResponse(DashboardModel):
      requests: list[RequestLogEntry] = Field(default_factory=list)
      total: int
      has_more: bool
      conversation: RequestLogConversation | None = None
  ```

- [x] **Step 4: Extend repository filters, totals, and cache identity**

  Add `conversation_id` to `list_recent` and `_build_filters`, append
  `RequestLog.conversation_id == conversation_id`, and add the value to the
  count-cache key. When it is present, execute
  `coalesce(sum(RequestLog.cost_usd), 0.0)` against the same complete filter
  list. Keep the existing top-level `total` as `conversation.requestCount` so
  pagination never changes it.

- [x] **Step 5: Thread the optional response through service and API**

  Add `conversation_id: str | None` to `RequestLogsService.list_recent` and the
  API endpoint query. Extend `RequestLogsPage` with
  `conversation: RequestLogConversation | None`; set it only when the filter is
  present and copy it into `RequestLogsResponse`.

- [x] **Step 6: Run the focused API and repository tests**

  Run the command from Step 2 without `-k`.

  Expected: PASS.

---

### Task 5: Add backend dashboard and report conversation counts

**Files:**
- Modify: `app/core/usage/types.py:128-136`
- Modify: `app/modules/usage/builders.py:46-193`
- Modify: `app/modules/request_logs/repository.py:155-248`
- Modify: `app/modules/dashboard/builders.py:70-104`
- Modify: `app/modules/dashboard/schemas.py:27-33`
- Modify: `app/modules/reports/repository.py:25-162`
- Modify: `app/modules/reports/schemas.py:8-53`
- Modify: `app/modules/reports/service.py:28-153`
- Test: `tests/integration/test_dashboard_overview.py`
- Test: `tests/unit/test_reports_repository.py`
- Test: `tests/unit/test_reports_service.py`
- Test: `tests/integration/test_reports_api.py`

**Interfaces:**
- Consumes: nullable `RequestLog.conversation_id`.
- Produces: dashboard `summary.metrics.conversations`, report
  `summary.totalConversations`, and daily-row `conversations`.

- [x] **Step 1: Write failing aggregate tests**

  Seed duplicate IDs, null IDs, an empty-string ID, a whitespace-only ID, and
  one conversation spanning two dates. Assert dashboard timeframe counts each
  distinct non-empty ID once; report summary counts the spanning conversation
  once overall; and each daily row counts it once on each applicable date.
  Assert null, empty-string, and whitespace-only IDs are excluded from every
  distinct aggregate, using SQL equivalent to
  `count(distinct(nullif(trim(RequestLog.conversation_id), '')))`. Keep
  existing normal-traffic exclusions and report account/model/user-agent
  filters active in assertions.

- [x] **Step 2: Run focused aggregate tests and confirm failure**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/integration/test_dashboard_overview.py tests/unit/test_reports_repository.py tests/unit/test_reports_service.py tests/integration/test_reports_api.py -k "conversation" -v
  ```

  Expected: FAIL because count fields are absent.

- [x] **Step 3: Extend dashboard activity aggregation**

  Add `conversation_count: int` to `RequestActivityAggregate` and
  `ActivityMetricsSummary`. Select
  `count(distinct(RequestLog.conversation_id))` in both activity aggregate
  methods, excluding null/blank IDs with the equivalent of
  `count(distinct(nullif(trim(RequestLog.conversation_id), '')))`; pass it
  through `build_activity_summaries` and
  `build_dashboard_overview_summary` as
  `DashboardUsageMetrics.conversations`.

- [x] **Step 4: Extend report summary and daily aggregation**

  Add `conversation_count` to repository aggregate rows and select the
  null/blank-excluding equivalent of
  `count(distinct(nullif(trim(RequestLog.conversation_id), '')))` in summary
  and daily queries. Add `total_conversations: int` to `ReportSummary` and
  `conversations: int` to `DailyReportRow`; map both in `ReportsService`.

- [x] **Step 5: Run the complete focused aggregate suites**

  Run the command from Step 2 without `-k`.

  Expected: PASS.

---

### Task 6: Implement dashboard conversation filtering UI

**Ownership:** Designer agent. Copy review remains with the orchestrator.

**Files:**
- Modify: `frontend/src/features/dashboard/schemas.ts:158-235`
- Modify: `frontend/src/features/dashboard/api.ts:28-98`
- Modify: `frontend/src/features/dashboard/hooks/use-request-logs.ts:13-197`
- Modify: `frontend/src/features/dashboard/components/dashboard-page.tsx:1-352`
- Modify: `frontend/src/features/dashboard/components/filters/request-filters.tsx:1-90`
- Modify: `frontend/src/features/dashboard/components/recent-requests-table.tsx:392-513`
- Modify: `frontend/src/features/dashboard/utils.ts`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/ko.json`
- Modify: `frontend/src/i18n/locales/zh-CN.json`
- Modify: `frontend/src/test/mocks/factories.ts`
- Test: `frontend/src/features/dashboard/schemas.test.ts`
- Test: `frontend/src/features/dashboard/hooks/use-request-logs.test.ts`
- Test: `frontend/src/features/dashboard/components/recent-requests-table.test.tsx`
- Test: `frontend/src/features/dashboard/components/dashboard-page.test.tsx`
- Test: `frontend/src/features/dashboard/utils.test.ts`
- Test: `frontend/src/__integration__/dashboard-flow.test.tsx`

**Interfaces:**
- Consumes: Task 4 request-log fields/response and Task 5
  `summary.metrics.conversations`.
- Produces: browser URL key `conversationId`, API wire key `conversation_id`,
  removable filter badge, detail-dialog click behavior, summary box, and
  Conversations dashboard stat.

- [x] **Step 1: Write failing schema, URL-state, and interaction tests**

  Cover parsing row-level `conversationId` and response-level `conversation`;
  browser URL parse/write/clear for one `conversationId`; offset reset on set
  and clear; detail click closing the dialog; badge position/dismissal; summary
  placement/copy/filter suffix; and stat ordering between Est. API Cost and
  Error Rate, including the optional burn-rate card case.

- [x] **Step 2: Run focused dashboard tests and confirm failure**

  Run:

  ```bash
  bun run --cwd frontend test -- src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-request-logs.test.ts src/features/dashboard/components/recent-requests-table.test.tsx src/features/dashboard/components/dashboard-page.test.tsx src/features/dashboard/utils.test.ts
  ```

  Expected: FAIL because conversation fields and controls are absent.

- [x] **Step 3: Extend frontend schemas, API parameters, and URL state**

  Add nullable `conversationId` to `RequestLogSchema`, nullable
  `{ requestCount, aggregatedCostUsd }` to `RequestLogsResponseSchema`, and
  nullable `conversationId` to `FilterStateSchema`/default state. Use
  `conversationId` in browser search params and query state; map it to
  `conversation_id` only in `getRequestLogs`.

- [x] **Step 4: Add detail click, badge, and summary box**

  Place Client IP and Conversation ID in a responsive two-column row. Render a
  present ID as a semantic button with no underline; on click close the dialog,
  preserve other filters, set the ID, and reset offset. Place an outline badge
  between Statuses and Reset; its accessible dismiss button clears only the ID
  and offset. Insert a semantic card row between filters and the table using:

  `The conversation {id} runs {count} request(s), cost = {cost} — filters: {active filters}` with ID, count, and cost rendered as styled inline-code values.

  Omit the suffix when no non-conversation filters are active. Show full IDs in
  tooltips/accessibility text when visual truncation is required.

- [x] **Step 5: Add the dashboard Conversations stat**

  Read `summary.metrics.conversations`, format it as an integer, and insert the
  card after Est. API Cost and before the optional burn-rate card/Error Rate.
  Reuse an installed Lucide icon and existing stat-card tokens; do not add a
  custom card component.

- [x] **Step 6: Add grounded localized copy**

  Add equivalent keys for Conversation, Conversation ID, copy action,
  Conversations with timeframe, Distinct conversations, the summary sentence,
  active-filter suffix, and dismiss action in all three existing locale files.
  Preserve the approved English sentence and normal wording in Korean and
  Simplified Chinese.

- [x] **Step 7: Run dashboard tests, typecheck, and lint**

  Run:

  ```bash
  bun run --cwd frontend test -- src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-request-logs.test.ts src/features/dashboard/components/recent-requests-table.test.tsx src/features/dashboard/components/dashboard-page.test.tsx src/features/dashboard/utils.test.ts src/__integration__/dashboard-flow.test.tsx
  bun run --cwd frontend typecheck
  bun run --cwd frontend lint
  ```

  Expected: PASS with no TypeScript or ESLint errors.

---

### Task 7: Implement report conversation statistics UI

**Ownership:** Reuse the Task 6 designer session to preserve visual intent.

**Files:**
- Modify: `frontend/src/features/reports/schemas.ts:3-48`
- Modify: `frontend/src/features/reports/daily-series.ts`
- Modify: `frontend/src/features/reports/components/reports-summary-cards.tsx:1-117`
- Modify: `frontend/src/features/reports/components/daily-detail-table.tsx:1-249`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/ko.json`
- Modify: `frontend/src/i18n/locales/zh-CN.json`
- Test: `frontend/src/features/reports/schemas.test.ts`
- Test: `frontend/src/features/reports/components/reports-summary-cards.test.tsx`
- Test: `frontend/src/features/reports/components/daily-detail-table.test.tsx`

**Interfaces:**
- Consumes: report `summary.totalConversations` and daily-row `conversations`
  from Task 5.
- Produces: Conversations report summary card and sortable/exportable daily
  Conversations column.

- [x] **Step 1: Write failing report UI tests**

  Assert schema parsing, the Conversations card immediately after Requests, the
  daily column between Reqs and Input Tokens, numeric sorting, zero-filled gap
  rows, and CSV header/value export.

- [x] **Step 2: Run focused report tests and confirm failure**

  Run:

  ```bash
  bun run --cwd frontend test -- src/features/reports/schemas.test.ts src/features/reports/components/reports-summary-cards.test.tsx src/features/reports/components/daily-detail-table.test.tsx
  ```

  Expected: FAIL because report conversation fields are absent.

- [x] **Step 3: Extend schemas and zero-filled rows**

  Add `totalConversations: z.number()` to `ReportSummarySchema`, add
  `conversations: z.number()` between requests and token fields in
  `DailyReportRowSchema`, and set `conversations: 0` in `createZeroRow`.

- [x] **Step 4: Add the report card and daily column**

  Add a fourth summary card immediately after Requests using existing card
  layout/number formatting. Add `conversations` to `SortKey`, column widths,
  header, body cells, and CSV output between Reqs and Input Tokens. Keep the
  table horizontally usable at existing responsive breakpoints.

- [x] **Step 5: Add localized report copy and verify UI**

  Add Conversations/Distinct conversations and daily table/CSV labels in all
  locale files, then run:

  ```bash
  bun run --cwd frontend test -- src/features/reports/schemas.test.ts src/features/reports/components/reports-summary-cards.test.tsx src/features/reports/components/daily-detail-table.test.tsx
  bun run --cwd frontend typecheck
  bun run --cwd frontend lint
  ```

  Expected: PASS.

---

### Task 8: Close the evidence path and synchronize specifications

**Files:**
- Update: `openspec/changes/support-conversation-grouping/tasks.md`
- Sync after verification: `openspec/specs/proxy-runtime-observability/spec.md`
- Sync after verification: `openspec/specs/query-caching/spec.md`
- Sync after verification: `openspec/specs/frontend-architecture/spec.md`

**Interfaces:**
- Consumes: Completed Tasks 1-7.
- Produces: focused evidence for detection, persistence, filtering,
  aggregation, UI behavior, migration safety, and OpenSpec coherence.

- [x] **Step 1: Run focused backend evidence**

  ```bash
  .venv/bin/python -m pytest tests/unit/test_proxy_utils.py -k "conversation or request_log_useragent_fields" -v
  .venv/bin/python -m pytest tests/unit/test_request_logs_repository.py tests/integration/test_request_logs_api.py tests/integration/test_request_logs_filters.py tests/integration/test_request_logs_list_count.py -v
  .venv/bin/python -m pytest tests/integration/test_proxy_websocket_responses.py -k "conversation" -v
  .venv/bin/python -m pytest tests/integration/test_dashboard_overview.py tests/unit/test_reports_repository.py tests/unit/test_reports_service.py tests/integration/test_reports_api.py -v
  .venv/bin/python -m pytest tests/integration/test_migrations.py -v
  ```

  Expected: PASS.

- [x] **Step 2: Run focused frontend evidence**

  ```bash
  bun run --cwd frontend test -- src/features/dashboard/schemas.test.ts src/features/dashboard/hooks/use-request-logs.test.ts src/features/dashboard/components/recent-requests-table.test.tsx src/features/dashboard/components/dashboard-page.test.tsx src/features/dashboard/utils.test.ts src/__integration__/dashboard-flow.test.tsx src/features/reports/schemas.test.ts src/features/reports/components/reports-summary-cards.test.tsx src/features/reports/components/daily-detail-table.test.tsx
  bun run --cwd frontend typecheck
  bun run --cwd frontend lint
  bun run --cwd frontend build
  ```

  Expected: PASS.

- [x] **Step 3: Run backend static checks on changed files**

  Run:

  ```bash
  uv run ruff check $(git diff --name-only --diff-filter=ACMRT -- '*.py')
  uv run ruff format --check $(git diff --name-only --diff-filter=ACMRT -- '*.py')
  uv run ty check
  ```

  Expected: exit code 0.

- [x] **Step 4: Verify Alembic and OpenSpec state**

  Run:

  ```bash
  .venv/bin/python -c "from pathlib import Path; from alembic.config import Config; from alembic.script import ScriptDirectory; config = Config(); config.set_main_option('script_location', str(Path('app/db/alembic').resolve())); heads = ScriptDirectory.from_config(config).get_heads(); assert len(heads) == 1, heads; print(heads[0])"
  openspec validate --specs
  ```

  Expected: one Alembic head and successful OpenSpec validation.

- [x] **Step 5: Review the final diff against the approved design**

  Confirm every request-log path carries the field, no unsupported harness is
  grouped, summary values match active filters, dashboard/report order matches
  the design, no dependency or unrelated refactor was added, and no active
  filter or migration behavior regressed.

- [x] **Step 6: Sync verified delta specs**

  Run the repository OpenSpec sync workflow for
  `support-conversation-grouping`, re-run `openspec validate --specs`, and mark
  every completed checkbox in this file. Do not archive until verification is
  complete and the user requests finalization.


---

### Task 9: Add dashboard conversation trendline and formatted summary copy

**Files:**
- Modify: `app/core/usage/types.py`
- Modify: `app/modules/request_logs/repository.py`
- Modify: `app/modules/dashboard/repository.py`
- Modify: `app/modules/dashboard/service.py`
- Modify: `app/modules/usage/builders.py`
- Modify: `app/modules/usage/schemas.py`
- Modify: `frontend/src/features/dashboard/schemas.ts`
- Modify: `frontend/src/features/dashboard/utils.ts`
- Modify: `frontend/src/features/dashboard/components/dashboard-page.tsx`
- Modify: `frontend/src/features/reports/components/reports-summary-cards.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/ko.json`
- Modify: `frontend/src/i18n/locales/zh-CN.json`
- Test: `tests/unit/test_request_logs_repository.py`
- Test: `tests/unit/test_dashboard_trends.py`
- Test: `tests/integration/test_dashboard_overview.py`
- Test: `frontend/src/features/dashboard/components/dashboard-page.test.tsx`
- Test: `frontend/src/features/dashboard/utils.test.ts`
- Test: `frontend/src/features/reports/components/reports-summary-cards.test.tsx`

**Interfaces:**
- Consumes: persisted normalized conversation IDs and existing dashboard
  timeframe bucket configuration.
- Produces: `MetricsTrends.conversations`, a dashboard Conversations sparkline,
  and a summary sentence with separate inline-code ID/count/cost values.

- [x] **Step 1: Verify failing regression tests were observed before production edits**

  Confirm the backend and frontend specialists recorded red tests for the missing
  bucket aggregate, empty dashboard trend, duplicate copy, and plain summary
  format before their production changes. If a red-test record is unavailable,
  reproduce one focused failure before proceeding.

- [x] **Step 2: Verify backend trend behavior**

  Run the repository, builder, and dashboard integration tests. Confirm the
  conversation trend has the configured bucket length, de-duplicates IDs across
  models/service tiers, excludes warmups/blanks, zero-fills missing buckets, and
  does not replace the exact summary total.

- [x] **Step 3: Verify frontend presentation behavior**

  Run dashboard/report component tests and inspect the rendered summary DOM.
  Confirm the Conversations card has a trend and no `distinct` metadata, the
  report card has no `distinct` subtitle, and the summary contains exactly three
  `code` elements in ID/count/cost order with `cost =` punctuation and no literal
  backticks.

- [x] **Step 4: Validate and close this task**

  Run `openspec validate --specs`, focused backend/frontend tests, frontend
  typecheck/lint, `git diff --check`, and the final diff review. Mark this task's
  steps complete only when every command passes. Do not archive the change in
  this task.
