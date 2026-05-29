# Reports Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Nova página `/reports` no dashboard com gráficos de custo/tokens por dia, distribuição por modelo, tabela detalhada, e filtros por período/conta/modelo.

**Architecture:** Backend: novo módulo FastAPI `app/modules/reports/` com endpoint `GET /api/reports` que agrega dados da tabela `request_logs`. Frontend: novo feature module `frontend/src/features/reports/` com Recharts + TanStack Query seguindo padrões existentes.

**Tech Stack:** Python 3.13+ / FastAPI / SQLAlchemy 2.0 async / React 19 / Recharts / TanStack Query 5 / Tailwind 4 / shadcn/ui

---

## File Structure

```
Create:
  app/modules/reports/
    __init__.py
    schemas.py             # Pydantic response models (DashboardModel)
    repository.py          # SQLAlchemy aggregation queries
    service.py             # Business logic / wiring
    api.py                 # FastAPI router: GET /api/reports
  frontend/src/features/reports/
    schemas.ts             # Zod schemas for API response
    api.ts                 # API call functions
    hooks/use-reports.ts   # TanStack Query hook
    components/
      reports-page.tsx     # Main page layout
      reports-filters.tsx  # Filter bar (date range, account, model)
      reports-summary-cards.tsx
      cost-per-day-chart.tsx
      tokens-per-day-chart.tsx
      model-distribution-donut.tsx
      daily-detail-table.tsx

Modify:
  app/dependencies.py      # Add ReportsContext + get_reports_context
  app/main.py              # import + app.include_router(reports_api.router)
  frontend/src/App.tsx     # Route for /reports
  frontend/src/components/layout/app-header.tsx  # NAV_ITEMS entry
```

---

### Task 1: Backend — schemas.py

**Files:**
- Create: `app/modules/reports/__init__.py` (empty)
- Create: `app/modules/reports/schemas.py`

- [ ] **Create __init__.py**

```bash
touch app/modules/reports/__init__.py
```

- [ ] **Create schemas.py**

```python
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DailyReportRow(DashboardModel):
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int
    error_count: int = 0


class ModelCostEntry(DashboardModel):
    model: str
    cost_usd: float
    percentage: float = 0.0


class AccountCostEntry(DashboardModel):
    account_id: str
    alias: str | None = None
    cost_usd: float = 0.0
    requests: int = 0


class ReportSummary(DashboardModel):
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_requests: int
    total_errors: int
    active_accounts: int
    avg_cost_per_day: float = 0.0
    avg_requests_per_day: float = 0.0


class ReportsResponse(DashboardModel):
    summary: ReportSummary
    daily: list[DailyReportRow] = Field(default_factory=list)
    by_model: list[ModelCostEntry] = Field(default_factory=list)
    by_account: list[AccountCostEntry] = Field(default_factory=list)
```

---

### Task 2: Backend — repository.py

**Files:**
- Create: `app/modules/reports/repository.py`

- [ ] **Create repository.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, String, and_, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog


@dataclass(frozen=True)
class DailyAggregateRow:
    date: str
    request_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int


@dataclass(frozen=True)
class ModelAggregateRow:
    model: str
    cost_usd: float


@dataclass(frozen=True)
class AccountAggregateRow:
    account_id: str
    alias: str | None
    cost_usd: float
    request_count: int


class ReportsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def aggregate_daily(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[DailyAggregateRow]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            date_expr = func.date(RequestLog.requested_at)
        else:
            date_expr = cast(RequestLog.requested_at, String).substring(0, 10)
        date_col = date_expr.label("date")

        conditions: list = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            RequestLog.deleted_at.is_(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                date_col,
                func.count().label("request_count"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)), 0
                ).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count(func.distinct(RequestLog.account_id)).label("active_accounts"),
            )
            .where(and_(*conditions))
            .group_by(date_col)
            .order_by(date_col)
        )
        result = await self._session.execute(stmt)
        return [
            DailyAggregateRow(
                date=row.date,
                request_count=int(row.request_count),
                error_count=int(row.error_count),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                cost_usd=float(row.cost_usd or 0.0),
                active_accounts=int(row.active_accounts),
            )
            for row in result.all()
        ]

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[ModelAggregateRow]:
        conditions: list = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            RequestLog.deleted_at.is_(None),
            RequestLog.cost_usd.is_not(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                RequestLog.model,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.model)
            .order_by(func.sum(RequestLog.cost_usd).desc())
        )
        result = await self._session.execute(stmt)
        return [
            ModelAggregateRow(model=row.model, cost_usd=float(row.cost_usd or 0.0))
            for row in result.all()
        ]

    async def aggregate_by_account(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[AccountAggregateRow]:
        conditions: list = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            RequestLog.deleted_at.is_(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                RequestLog.account_id,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.account_id)
            .order_by(func.sum(RequestLog.cost_usd).desc())
        )
        result = await self._session.execute(stmt)

        # Fetch account aliases
        rows = result.all()
        account_ids_list = [r.account_id for r in rows if r.account_id]
        aliases: dict[str, str] = {}
        if account_ids_list:
            alias_result = await self._session.execute(
                select(Account.id, Account.alias).where(Account.id.in_(account_ids_list))
            )
            for row in alias_result.all():
                if row.alias:
                    aliases[row.id] = row.alias

        return [
            AccountAggregateRow(
                account_id=row.account_id or "",
                alias=aliases.get(row.account_id) if row.account_id else None,
                cost_usd=float(row.cost_usd or 0.0),
                request_count=int(row.request_count),
            )
            for row in rows
            if row.account_id
        ]
```

---

### Task 3: Backend — service.py

**Files:**
- Create: `app/modules/reports/service.py`

- [ ] **Create service.py**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import floor

from app.modules.reports.repository import ReportsRepository
from app.modules.reports.schemas import (
    AccountCostEntry,
    DailyReportRow,
    ModelCostEntry,
    ReportSummary,
    ReportsResponse,
)


class ReportsService:
    def __init__(self, repository: ReportsRepository) -> None:
        self._repository = repository

    async def get_reports(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> ReportsResponse:
        now = datetime.now(timezone.utc)
        if end_date is None:
            end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        if start_date is None:
            start_date = (end_date - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        daily = await self._repository.aggregate_daily(start_date, end_date, account_ids, model)
        by_model = await self._repository.aggregate_by_model(start_date, end_date, account_ids, model)
        by_account = await self._repository.aggregate_by_account(start_date, end_date, account_ids, model)

        total_cost = sum(d.cost_usd for d in daily)
        total_input = sum(d.input_tokens for d in daily)
        total_output = sum(d.output_tokens for d in daily)
        total_cached = sum(d.cached_input_tokens for d in daily)
        total_requests = sum(d.request_count for d in daily)
        total_errors = sum(d.error_count for d in daily)
        active_accounts = max((d.active_accounts for d in daily), default=0)
        day_count = len(daily) or 1

        model_total = sum(m.cost_usd for m in by_model)

        return ReportsResponse(
            summary=ReportSummary(
                total_cost_usd=round(total_cost, 4),
                total_input_tokens=total_input,
                total_output_tokens=total_output,
                total_cached_tokens=total_cached,
                total_requests=total_requests,
                total_errors=total_errors,
                active_accounts=active_accounts,
                avg_cost_per_day=round(total_cost / day_count, 4),
                avg_requests_per_day=round(total_requests / day_count, 2),
            ),
            daily=[
                DailyReportRow(
                    date=d.date,
                    requests=d.request_count,
                    input_tokens=d.input_tokens,
                    output_tokens=d.output_tokens,
                    cached_input_tokens=d.cached_input_tokens,
                    cost_usd=round(d.cost_usd, 4),
                    active_accounts=d.active_accounts,
                    error_count=d.error_count,
                )
                for d in daily
            ],
            by_model=[
                ModelCostEntry(
                    model=m.model,
                    cost_usd=round(m.cost_usd, 4),
                    percentage=round((m.cost_usd / model_total * 100), 1) if model_total > 0 else 0,
                )
                for m in by_model
            ],
            by_account=[
                AccountCostEntry(
                    account_id=a.account_id,
                    alias=a.alias,
                    cost_usd=round(a.cost_usd, 4),
                    requests=a.request_count,
                )
                for a in by_account
            ],
        )
```

---

### Task 4: Backend — api.py + wiring

**Files:**
- Create: `app/modules/reports/api.py`
- Modify: `app/dependencies.py`
- Modify: `app/main.py`

- [ ] **Create api.py**

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import ReportsContext, get_reports_context
from app.modules.reports.schemas import ReportsResponse

router = APIRouter(
    prefix="/api/reports",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=ReportsResponse)
async def get_reports(
    context: ReportsContext = Depends(get_reports_context),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
) -> ReportsResponse:
    start = (
        datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        if start_date
        else None
    )
    end = (
        datetime.combine(end_date, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
        if end_date
        else None
    )
    return await context.service.get_reports(
        start_date=start,
        end_date=end,
        account_ids=account_id,
        model=model,
    )
```

- [ ] **Add ReportsContext to dependencies.py**

Add after `StickySessionsContext` class (around line 121):

```python
@dataclass(slots=True)
class ReportsContext:
    session: AsyncSession
    repository: ReportsRepository
    service: ReportsService
```

Add import at top of `app/dependencies.py`:
```python
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.service import ReportsService
```

Add factory function after the existing context factories (around line 250):

```python
def get_reports_context(
    session: AsyncSession = Depends(get_session),
) -> ReportsContext:
    repository = ReportsRepository(session)
    service = ReportsService(repository)
    return ReportsContext(session=session, repository=repository, service=service)
```

- [ ] **Register router in main.py**

Add import in `app/main.py`:
```python
from app.modules.reports import api as reports_api
```

Add `app.include_router(reports_api.router)` after line 377 (after `api_keys_api.router`)

- [ ] **Run type check**

```bash
uv run pyright
```

---

### Task 5: Frontend — schemas.ts + api.ts

**Files:**
- Create: `frontend/src/features/reports/`
- Create: `frontend/src/features/reports/schemas.ts`
- Create: `frontend/src/features/reports/api.ts`

- [ ] **Create feature directory**

```bash
mkdir -p frontend/src/features/reports/hooks frontend/src/features/reports/components
```

- [ ] **Create schemas.ts**

```typescript
import { z } from "zod";

export const DailyReportRowSchema = z.object({
  date: z.string(),
  requests: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cachedInputTokens: z.number(),
  costUsd: z.number(),
  activeAccounts: z.number(),
  errorCount: z.number(),
});

export const ModelCostEntrySchema = z.object({
  model: z.string(),
  costUsd: z.number(),
  percentage: z.number(),
});

export const AccountCostEntrySchema = z.object({
  accountId: z.string(),
  alias: z.string().nullable(),
  costUsd: z.number(),
  requests: z.number(),
});

export const ReportSummarySchema = z.object({
  totalCostUsd: z.number(),
  totalInputTokens: z.number(),
  totalOutputTokens: z.number(),
  totalCachedTokens: z.number(),
  totalRequests: z.number(),
  totalErrors: z.number(),
  activeAccounts: z.number(),
  avgCostPerDay: z.number(),
  avgRequestsPerDay: z.number(),
});

export const ReportsResponseSchema = z.object({
  summary: ReportSummarySchema,
  daily: z.array(DailyReportRowSchema),
  byModel: z.array(ModelCostEntrySchema),
  byAccount: z.array(AccountCostEntrySchema),
});

export type DailyReportRow = z.infer<typeof DailyReportRowSchema>;
export type ModelCostEntry = z.infer<typeof ModelCostEntrySchema>;
export type AccountCostEntry = z.infer<typeof AccountCostEntrySchema>;
export type ReportSummary = z.infer<typeof ReportSummarySchema>;
export type ReportsResponse = z.infer<typeof ReportsResponseSchema>;
```

- [ ] **Create api.ts**

```typescript
import { get } from "@/lib/api-client";
import { ReportsResponseSchema } from "./schemas";

export type ReportsParams = {
  startDate?: string;
  endDate?: string;
  accountId?: string[];
  model?: string;
};

export function getReports(params: ReportsParams = {}) {
  const query = new URLSearchParams();
  if (params.startDate) query.set("start_date", params.startDate);
  if (params.endDate) query.set("end_date", params.endDate);
  if (params.model) query.set("model", params.model);
  if (params.accountId) {
    for (const id of params.accountId) {
      query.append("account_id", id);
    }
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`/api/reports${suffix}`, ReportsResponseSchema);
}
```

---

### Task 6: Frontend — use-reports hook

**Files:**
- Create: `frontend/src/features/reports/hooks/use-reports.ts`

- [ ] **Create hook**

```typescript
import { useQuery } from "@tanstack/react-query";
import { getReports, type ReportsParams } from "../api";

type ReportsFilterState = {
  startDate: string | undefined;
  endDate: string | undefined;
  accountId: string[];
  model: string | undefined;
};

export function useReports(filters: ReportsFilterState) {
  return useQuery({
    queryKey: ["reports", filters],
    queryFn: () =>
      getReports({
        startDate: filters.startDate,
        endDate: filters.endDate,
        accountId: filters.accountId.length > 0 ? filters.accountId : undefined,
        model: filters.model || undefined,
      }),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
}
```

---

### Task 7: Frontend — summary cards component

**Files:**
- Create: `frontend/src/features/reports/components/reports-summary-cards.tsx`

- [ ] **Create summary cards component**

```typescript
import type { ReportSummary } from "../schemas";

export type ReportsSummaryCardsProps = {
  summary: ReportSummary;
};

export function ReportsSummaryCards({ summary }: ReportsSummaryCardsProps) {
  const cards = [
    {
      label: "Custo Total",
      value: `$${summary.totalCostUsd.toFixed(2)}`,
      sub: `média $${summary.avgCostPerDay.toFixed(2)}/dia`,
    },
    {
      label: "Tokens",
      value: formatNumber(summary.totalInputTokens + summary.totalOutputTokens),
      sub: `Input ${formatNumber(summary.totalInputTokens)} · Output ${formatNumber(summary.totalOutputTokens)}`,
    },
    {
      label: "Requisições",
      value: formatNumber(summary.totalRequests),
      sub: `média ${summary.avgRequestsPerDay.toFixed(0)}/dia · ${summary.activeAccounts} contas`,
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {cards.map((card) => (
        <div key={card.label} className="rounded-xl border bg-card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {card.label}
          </div>
          <div className="mt-1 text-[1.625rem] font-semibold tracking-[-0.02em] text-foreground">
            {card.value}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">{card.sub}</div>
        </div>
      ))}
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
```

---

### Task 8: Frontend — cost per day chart

**Files:**
- Create: `frontend/src/features/reports/components/cost-per-day-chart.tsx`

- [ ] **Create cost chart component**

```typescript
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { DailyReportRow } from "../schemas";

export type CostPerDayChartProps = {
  data: DailyReportRow[];
};

export function CostPerDayChart({ data }: CostPerDayChartProps) {
  const chartData = data.map((d) => ({
    date: d.date.slice(5),
    cost: d.costUsd,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Custo por Dia</div>
      <div className="mt-4 h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
            />
            <Tooltip
              contentStyle={{
                borderRadius: "8px",
                border: "1px solid hsl(var(--border))",
                background: "hsl(var(--popover))",
              }}
              formatter={(value: number) => [`$${value.toFixed(2)}`, "Custo"]}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke="#3b82f6"
              strokeWidth={2}
              fill="url(#costGrad)"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
```

---

### Task 9: Frontend — tokens per day chart

**Files:**
- Create: `frontend/src/features/reports/components/tokens-per-day-chart.tsx`

- [ ] **Create tokens chart component**

```typescript
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { DailyReportRow } from "../schemas";

export type TokensPerDayChartProps = {
  data: DailyReportRow[];
};

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function TokensPerDayChart({ data }: TokensPerDayChartProps) {
  const chartData = data.map((d) => ({
    date: d.date.slice(5),
    input: d.inputTokens,
    output: d.outputTokens,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Tokens por Dia</div>
      <div className="mt-4 h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="inputGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="outputGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ec4899" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#ec4899" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={formatTokens}
            />
            <Tooltip
              contentStyle={{
                borderRadius: "8px",
                border: "1px solid hsl(var(--border))",
                background: "hsl(var(--popover))",
              }}
              formatter={(value: number, name: string) => [
                formatTokens(value),
                name === "input" ? "Input" : "Output",
              ]}
            />
            <Area
              type="monotone"
              dataKey="input"
              stroke="#8b5cf6"
              strokeWidth={2}
              fill="url(#inputGrad)"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            />
            <Area
              type="monotone"
              dataKey="output"
              stroke="#ec4899"
              strokeWidth={2}
              fill="url(#outputGrad)"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
```

---

### Task 10: Frontend — model distribution donut

**Files:**
- Create: `frontend/src/features/reports/components/model-distribution-donut.tsx`

- [ ] **Create model donut component**

```typescript
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { ModelCostEntry } from "../schemas";

export type ModelDistributionDonutProps = {
  data: ModelCostEntry[];
};

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"];

export function ModelDistributionDonut({ data }: ModelDistributionDonutProps) {
  const total = data.reduce((sum, m) => sum + m.costUsd, 0);

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Distribuição por Modelo</div>
      <div className="mt-4 flex items-center gap-4">
        <div className="h-[140px] w-[140px] shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="costUsd"
                nameKey="model"
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={65}
                strokeWidth={0}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  borderRadius: "8px",
                  border: "1px solid hsl(var(--border))",
                  background: "hsl(var(--popover))",
                }}
                formatter={(value: number) => [`$${value.toFixed(2)}`, "Custo"]}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5 text-xs">
          {data.map((entry, i) => (
            <div
              key={entry.model}
              className="flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ background: COLORS[i % COLORS.length] }}
                />
                <span className="text-foreground">{entry.model}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-muted-foreground">{entry.percentage}%</span>
                <span className="font-medium text-foreground">${entry.costUsd.toFixed(2)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

---

### Task 11: Frontend — daily detail table

**Files:**
- Create: `frontend/src/features/reports/components/daily-detail-table.tsx`

- [ ] **Create table component**

```typescript
import type { DailyReportRow } from "../schemas";

export type DailyDetailTableProps = {
  data: DailyReportRow[];
};

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function DailyDetailTable({ data }: DailyDetailTableProps) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-foreground">Detalhamento por Dia</div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">Dia</th>
              <th className="pb-2 pr-4 text-right font-medium">Req</th>
              <th className="pb-2 pr-4 text-right font-medium">Tokens In</th>
              <th className="pb-2 pr-4 text-right font-medium">Tokens Out</th>
              <th className="pb-2 pr-4 text-right font-medium">Custo</th>
              <th className="pb-2 text-right font-medium">Contas</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.date} className="border-b border-border/50 last:border-0">
                <td className="py-2.5 pr-4 font-medium text-foreground">
                  {formatDate(row.date)}
                </td>
                <td className="py-2.5 pr-4 text-right text-foreground">
                  {row.requests}
                </td>
                <td className="py-2.5 pr-4 text-right text-foreground">
                  {formatTokens(row.inputTokens)}
                </td>
                <td className="py-2.5 pr-4 text-right text-foreground">
                  {formatTokens(row.outputTokens)}
                </td>
                <td className="py-2.5 pr-4 text-right font-medium text-emerald-600 dark:text-emerald-400">
                  ${row.costUsd.toFixed(2)}
                </td>
                <td className="py-2.5 text-right text-muted-foreground">
                  {row.activeAccounts}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}
```

---

### Task 12: Frontend — filters bar

**Files:**
- Create: `frontend/src/features/reports/components/reports-filters.tsx`

- [ ] **Create filters component**

```typescript
import { Button } from "@/components/ui/button";

export type ReportsFiltersState = {
  startDate: string;
  endDate: string;
  accountId: string[];
  model: string;
};

export type ReportsFiltersProps = {
  filters: ReportsFiltersState;
  onFiltersChange: (filters: ReportsFiltersState) => void;
};

const PRESETS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function ReportsFilters({ filters, onFiltersChange }: ReportsFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3">
      {PRESETS.map((preset) => (
        <Button
          key={preset.days}
          variant="outline"
          size="sm"
          onClick={() =>
            onFiltersChange({
              ...filters,
              startDate: daysAgoISO(preset.days),
              endDate: todayISO(),
            })
          }
        >
          {preset.label}
        </Button>
      ))}

      <div className="ml-auto flex items-center gap-2">
        <input
          type="date"
          value={filters.startDate}
          onChange={(e) =>
            onFiltersChange({ ...filters, startDate: e.target.value })
          }
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
        <span className="text-xs text-muted-foreground">—</span>
        <input
          type="date"
          value={filters.endDate}
          onChange={(e) =>
            onFiltersChange({ ...filters, endDate: e.target.value })
          }
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
      </div>
    </div>
  );
}
```

---

### Task 13: Frontend — reports page (main layout)

**Files:**
- Create: `frontend/src/features/reports/components/reports-page.tsx`

- [ ] **Create page layout**

```typescript
import { useState } from "react";
import { useReports } from "@/features/reports/hooks/use-reports";
import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { ReportsSummaryCards } from "./reports-summary-cards";
import { CostPerDayChart } from "./cost-per-day-chart";
import { TokensPerDayChart } from "./tokens-per-day-chart";
import { ModelDistributionDonut } from "./model-distribution-donut";
import { DailyDetailTable } from "./daily-detail-table";

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const DEFAULT_FILTERS: ReportsFiltersState = {
  startDate: daysAgoISO(7),
  endDate: todayISO(),
  accountId: [],
  model: "",
};

export function ReportsPage() {
  const [filters, setFilters] = useState<ReportsFiltersState>(DEFAULT_FILTERS);
  const { data, isLoading } = useReports(filters);

  return (
    <div className="mx-auto w-full max-w-[1500px] flex-1 space-y-6 px-4 py-8 sm:px-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Relatório de Custo
        </h1>
        <p className="text-sm text-muted-foreground">
          Histórico de utilização por período
        </p>
      </div>

      <ReportsFilters filters={filters} onFiltersChange={setFilters} />

      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
          Carregando...
        </div>
      ) : data ? (
        <>
          <ReportsSummaryCards summary={data.summary} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CostPerDayChart data={data.daily} />
            <TokensPerDayChart data={data.daily} />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <ModelDistributionDonut data={data.byModel} />
            </div>
            <div className="lg:col-span-2">
              <DailyDetailTable data={data.daily} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
```

---

### Task 14.5: Frontend — CSV export

Add CSV download button to `daily-detail-table.tsx`:

```typescript
// Add to imports
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";

// Add inside DailyDetailTable component, after the div with "Detalhamento por Dia":
<Button variant="outline" size="sm" className="h-7 gap-1 text-xs" onClick={() => exportCSV(data)}>
  <Download className="h-3 w-3" />
  CSV
</Button>

// Add helper function at bottom of file:
function exportCSV(rows: DailyReportRow[]) {
  const headers = ["Date", "Requests", "Input Tokens", "Output Tokens", "Cached Tokens", "Cost USD", "Active Accounts", "Errors"];
  const lines = rows.map((r) =>
    [r.date, r.requests, r.inputTokens, r.outputTokens, r.cachedInputTokens, r.costUsd.toFixed(4), r.activeAccounts, r.errorCount].join(","),
  );
  const csv = [headers.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `reports-${rows[0]?.date ?? "data"}-${rows[rows.length - 1]?.date ?? "data"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
```

---

### Task 15: Frontend — Register route + nav link

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/app-header.tsx`

- [ ] **Add route in App.tsx**

Add import:
```typescript
import { ReportsPage } from "@/features/reports/components/reports-page";
```

Add route after the `/dashboard` Route:
```typescript
<Route path="/reports" element={<ReportsPage />} />
```

- [ ] **Add nav link in app-header.tsx**

Add to `NAV_ITEMS` array between Dashboard and Accounts:
```typescript
const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/reports", label: "Reports" },
  { to: "/accounts", label: "Accounts" },
  { to: "/apis", label: "APIs" },
  { to: "/settings", label: "Settings" },
] as const;
```

---

### Task 15: Build + verify

- [ ] **Build frontend**

```bash
cd frontend && bun run build
```

- [ ] **Run backend type check**

```bash
uv run pyright
```

- [ ] **Run tests**

```bash
uv run pytest -x -q
```
