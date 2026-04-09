# 프론트엔드

이 프론트엔드는 Bun, Vite, React, TypeScript, SWC를 사용합니다.

## 준비

- Bun 1.3 이상

## 설치

```bash
cd frontend
bun install
```

## 개발 서버

```bash
bun run dev
```

기본 포트는 `5173`이며, 다음 경로를 FastAPI로 프록시합니다.

- `/api/*`
- `/v1/*`
- `/backend-api/*`
- `/health`

## 빌드

```bash
bun run build
```

빌드 결과물은 `../app/static` 에 생성됩니다.

## 품질 검사

```bash
bun run lint
bun run test
bun run test:coverage
```
