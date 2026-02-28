## 1. Backend firewall domain

- [x] 1.1 Добавить таблицу allowlist IP в ORM + Alembic migration
- [x] 1.2 Реализовать `app/modules/firewall` (schemas/repository/service/api) по текущим typing-конвенциям
- [x] 1.3 Добавить middleware firewall с проверкой только protected proxy-paths
- [x] 1.4 Прокинуть настройки `firewall_trust_proxy_headers` и `firewall_trusted_proxy_cidrs`
- [x] 1.5 Подключить firewall API router, middleware и DI context в `main.py` / `dependencies.py`

## 2. React dashboard integration

- [x] 2.1 Добавить feature `frontend/src/features/firewall` (schemas/api/hooks/components)
- [x] 2.2 Добавить route `/firewall` и пункт навигации Firewall в header
- [x] 2.3 Обеспечить CRUD UX: список IP, добавление, удаление, ошибки/loading

## 3. Tests

- [x] 3.1 Добавить/обновить backend unit tests для service и IP resolution
- [x] 3.2 Добавить backend integration tests для firewall API и middleware enforcement
- [x] 3.3 Добавить frontend tests (schema/api/hooks/component flow) для firewall feature

## 4. Spec delta

- [x] 4.1 Добавить delta-спеку `api-firewall`
- [x] 4.2 Обновить delta по `frontend-architecture` (роутинг + страница firewall)
- [ ] 4.3 Выполнить `openspec validate --specs`
