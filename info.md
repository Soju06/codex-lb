# План имплементации опциональной TOTP-авторизации при каждом входе в веб-панель

## 1. Цель и границы
- Добавить опциональный режим: при включении пользователь обязан пройти TOTP-проверку на каждом новом входе в веб-панель.
- Режим по умолчанию: выключен (обратная совместимость).
- Область: только dashboard (`/dashboard` и dashboard API), без влияния на proxy API-контракты для клиентов.

## 2. Архитектурное решение
- Добавить отдельный модуль `app/modules/dashboard_auth/`:
  - `api.py` — endpoints для логина/логаута/проверки сессии/TOTP setup.
  - `service.py` — бизнес-логика сессий и TOTP-валидации.
  - `repository.py` — чтение/обновление auth-полей в `dashboard_settings`.
  - `schemas.py` — строго типизированные Pydantic-схемы запросов/ответов.
- Подключить модуль через контекст в `app/dependencies.py` (по текущему DI-паттерну проекта).

## 3. Данные и миграции
- Расширить `DashboardSettings` (`app/db/models.py`) минимальным набором полей:
  - `totp_required_on_login: bool` (флаг опциональности).
  - `totp_secret_encrypted: bytes | None` (секрет TOTP, шифруется).
  - `totp_last_verified_step: int | None` (защита от replay в пределах timestep).
- Добавить Alembic-миграцию в `app/db/migrations/versions/`:
  - новые nullable поля;
  - `totp_required_on_login` с default `False`.
- Не дублировать состояние (`totp_enabled` отдельно не хранить): вычислять как `totp_secret_encrypted is not None`.

## 4. Backend поток аутентификации
- Ввести cookie-сессию панели (`HttpOnly`, `Secure` при HTTPS, `SameSite=Lax`) с признаком `totp_verified`.
- Добавить guard для dashboard маршрутов/API:
  - если сессии нет -> `401`/редирект на login-экран;
  - если `totp_required_on_login=True` и TOTP не пройден -> `401`.
- Endpoint-ы (пример):
  - `GET /api/dashboard-auth/session` — состояние сессии и требование TOTP.
  - `POST /api/dashboard-auth/login` — старт логина (создание промежуточной сессии).
  - `POST /api/dashboard-auth/totp/verify` — проверка 6-значного кода и повышение сессии до `totp_verified`.
  - `POST /api/dashboard-auth/totp/setup/start` — генерация секрета + `otpauth://` URI.
  - `POST /api/dashboard-auth/totp/setup/confirm` — подтверждение кода и сохранение секрета.
  - `POST /api/dashboard-auth/totp/disable` — отключение TOTP после успешной верификации текущей сессии.
  - `POST /api/dashboard-auth/logout` — удаление сессии.

## 5. TOTP-логика и безопасность
- Использовать RFC 6238 (30s timestep, 6 digits, SHA-1 совместимость с Google Authenticator/Authy).
- Секрет хранить только в зашифрованном виде (через существующий крипто-слой проекта).
- Принять ограниченное окно дрейфа времени (`-1/0/+1` timestep), но блокировать повторный прием того же timestep через `totp_last_verified_step`.
- Добавить rate limit на попытки ввода кода (например, in-memory счетчик по сессии/IP с backoff).
- Логировать только факт успеха/ошибки, без кода и без секрета.

## 6. Изменения в модуле settings
- Расширить:
  - `app/modules/settings/schemas.py` (добавить `totp_required_on_login: bool`, `totp_configured: bool`).
  - `app/modules/settings/service.py` и `repository.py` (чтение/обновление нового флага).
- `totp_configured` — вычисляемое поле ответа (из `totp_secret_encrypted`), не хранится отдельно.

## 7. Изменения фронтенда (`app/static/*`)
- Добавить login/TOTP gate до загрузки основного dashboard-контента.
- Добавить в Settings UI:
  - переключатель `Require TOTP on every login`;
  - блок onboarding TOTP: показать QR/секрет, поле подтверждения кода, кнопки enable/disable.
- Обработать состояния:
  - TOTP required but not configured -> нельзя включить флаг, показать явную ошибку.
  - expired session -> возврат на login gate.

## 8. Тестирование
- Unit (`tests/unit`):
  - генерация/проверка TOTP, replay-protection, time-drift window;
  - сервисные кейсы `required on/off`.
- Integration (`tests/integration`):
  - полный flow setup -> login -> totp verify -> доступ к dashboard API;
  - отрицательные кейсы (неверный код, повтор кода, выключенный TOTP).
- Обновить контрактные тесты settings API под новые поля.

## 9. Порядок внедрения
1. Миграция + модель `DashboardSettings`.
2. `dashboard_auth` модуль (schemas/repository/service/api) и DI-контекст.
3. Guard/middleware для dashboard endpoints.
4. Обновление settings API/сервиса.
5. Frontend login gate и settings TOTP UI.
6. Unit + integration тесты, регрессия существующих dashboard сценариев.

## 10. Критерии готовности (DoD)
- При `totp_required_on_login=false` текущий UX входа не ломается.
- При `totp_required_on_login=true` доступ к панели невозможен без успешной TOTP-проверки.
- Секрет не появляется в логах и не хранится в открытом виде.
- Все новые/измененные API контракты покрыты тестами и проходят CI.
