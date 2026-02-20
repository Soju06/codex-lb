## Why

В `dwnmf/codex-lb` есть рабочий API firewall (allowlist IP + middleware для proxy-маршрутов), но в текущем `Soju06/codex-lb` после React-миграции этот функционал отсутствует.

Нужно восстановить firewall в текущей кодовой базе: backend-контракты и enforcement, а также UI в React-приложении.

## What Changes

- Добавить backend firewall-модуль с CRUD API для allowlist IP (`/api/firewall/ips`)
- Добавить middleware, который применяет allowlist к protected proxy-роутам (`/backend-api/codex/*`, `/v1/*`)
- Добавить настройки определения клиентского IP через proxy headers с доверенными CIDR
- Добавить React-страницу `/firewall` и пункт навигации для управления allowlist
- Добавить миграцию БД для таблицы allowlist
- Добавить unit/integration/frontend тесты на новый контракт

## Capabilities

### Added Capabilities

- `api-firewall`: управление IP allowlist и enforcement на proxy endpoint-ах

### Modified Capabilities

- `frontend-architecture`: маршрут и навигация расширяются страницей Firewall

## Impact

- **Backend**: `app/core/config/settings.py`, `app/core/middleware/*`, `app/db/models.py`, `app/db/alembic/versions/*`, `app/modules/firewall/*`, `app/dependencies.py`, `app/main.py`
- **Frontend**: `frontend/src/App.tsx`, `frontend/src/components/layout/app-header.tsx`, `frontend/src/features/firewall/*`
- **Tests**: backend unit/integration + frontend unit/integration для firewall
