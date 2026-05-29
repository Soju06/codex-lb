# Relatório de Custo e Utilização — Design

## Resumo

Nova página `/reports` no dashboard do codex-lb para análise histórica de custo e utilização,
com gráficos por dia, filtros por conta/modelo/período, e granularidade superior à dashboard
agregada atual.

## Requisitos

1. Página dedicada acessível via navegação do dashboard (`/reports`)
2. Seletor de período customizado (date picker com presets: 7d, 30d, 90d, este mês)
3. Filtros por conta e por modelo
4. Gráfico de linhas: Custo por Dia (breakdown por conta opcional)
5. Gráfico de linhas: Tokens por Dia (input + output + cached)
6. Donut: Distribuição de Custo por Modelo
7. Tabela detalhada por dia: requests, tokens input/output, custo, contas ativas
8. Cards de resumo no topo: custo total, tokens totais, total de requests
9. Exportar dados da tabela como CSV
10. Seguir o design system existente (shadcn/ui, Tailwind, Recharts)

## Arquitetura

### Backend — Novo endpoint: `GET /api/reports`

```
GET /api/reports?start_date=2026-05-22&end_date=2026-05-29&account_id=optional&model=optional
```

**Resposta:**

```json
{
  "summary": {
    "total_cost_usd": 127.43,
    "total_input_tokens": 10100000,
    "total_output_tokens": 4100000,
    "total_cached_tokens": 1200000,
    "total_requests": 3847,
    "active_accounts": 8,
    "avg_cost_per_day": 18.20,
    "avg_requests_per_day": 550
  },
  "daily": [
    {
      "date": "2026-05-22",
      "requests": 523,
      "input_tokens": 1400000,
      "output_tokens": 520000,
      "cached_tokens": 180000,
      "cost_usd": 18.23,
      "active_accounts": 8
    }
  ],
  "by_model": [
    { "model": "gpt-4o", "cost_usd": 53.52, "percentage": 42 },
    { "model": "o3", "cost_usd": 35.68, "percentage": 28 }
  ],
  "by_account": [
    { "account_id": "...", "alias": "Conta A", "cost_usd": 42.10, "requests": 1200 }
  ]
}
```

Implementação:
- Novo módulo `app/modules/reports/` com router, service, schemas
- Queries SQL agregadas em `request_logs` com GROUP BY dia/modelo/conta
- Cache de 5 minutos para dados do mesmo período

### Frontend — Nova página em `frontend/src/features/reports/`

```
frontend/src/features/reports/
├── components/
│   ├── reports-page.tsx           # Layout principal
│   ├── reports-filters.tsx        # Barra de filtros
│   ├── reports-summary-cards.tsx  # 3 cards de resumo
│   ├── cost-per-day-chart.tsx     # Gráfico de custo por dia
│   ├── tokens-per-day-chart.tsx   # Gráfico de tokens por dia
│   ├── model-distribution-donut.tsx # Donut por modelo
│   └── daily-detail-table.tsx    # Tabela detalhada
├── hooks/
│   └── use-reports.ts            # React Query hook
└── schemas/
    └── reports-schema.ts         # Zod schemas
```

- Rota: `/reports` registrada em `App.tsx`
- Navegação: link na sidebar do dashboard
- Dados: TanStack React Query com refetch manual
- Gráficos: Recharts (seguindo padrão dos componentes existentes)

### Dados

Fonte primária: tabela `request_logs` (já contém `input_tokens`, `output_tokens`,
`cached_input_tokens`, `cost_usd`, `model`, `account_id`, `requested_at`).

A query agrupa por dia (`DATE(requested_at)`), conta e modelo, somando tokens e
custo. Períodos sem requisições não aparecem (zero fill não é necessário).

### Fluxo

1. Usuário navega para `/reports`
2. Frontend faz GET `/api/reports` com filtros padrão (7 dias)
3. Backend consulta `request_logs` com GROUP BY dia
4. Frontend renderiza cards, gráficos, donut e tabela
5. Usuário ajusta filtros → React Query refetch automático
6. Botão "Exportar CSV" baixa dados atuais como CSV

## Limitações

- Dados vêm apenas da tabela `request_logs` (não inclui `usage_history` que tem
  snapshots de créditos)
- Período máximo recomendado: 90 dias (performance da query em SQLite)
- Sem projeções (fora do escopo desta versão)
