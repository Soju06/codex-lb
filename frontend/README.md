# Frontend (Bun + Vite + React + TypeScript + SWC)

This frontend is built with Bun, Vite, React, TypeScript, and SWC.

## Prerequisites

- Bun 1.3+

## Setup

```bash
cd frontend
bun install
```

## Development

```bash
bun run dev
```

Vite dev server runs on port `5173` by default and proxies API routes to FastAPI:

- `/api/*`
- `/v1/*`
- `/backend-api/*`
- `/health`

## Build

```bash
bun run build
```

Production assets are emitted to `../app/static`.

## Quality

```bash
bun run lint
bun run test
bun run test:coverage
```
