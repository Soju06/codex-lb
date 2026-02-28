# Project Code Conventions

## 1. Typing & Data Contracts

- Prefer strict typing end-to-end. Avoid `dict`, `Mapping[str, object]`, and `object` in app/service/repository layers when the shape is known.
- Use explicit dataclasses or Pydantic models for internal payloads; convert to response schemas at the edge.
- ORM models should be passed through services instead of generic containers; avoid `getattr`/`[]` access on ORM results.
- Expose time values in dashboard APIs as ISO 8601 strings (`datetime` in schemas), not epoch numbers.
- If a test depends on a contract change (field name/type), update the test to match the new typed schema.

```python
# Bad
def get_user(id: int) -> dict:
    return {"name": user.name, "created": user.created_at.timestamp()}

# Good
class UserResponse(BaseModel):
    name: str
    created_at: datetime  # ISO 8601

def get_user(id: int) -> UserResponse:
    return UserResponse(name=user.name, created_at=user.created_at)
```

## 2. Anti-Patterns to Avoid

- **No Speculative Fallbacks**: Do not use multiple keys for the same configuration (e.g., `os.getenv("A") or os.getenv("B")`). Pick one canonical name and stick to it.
- **Single Source of Truth**: Do not create redundant fields in data models (JSON/DB) that represent the same state. Calculate derived values dynamically.
- **Fail Fast**: Do not clutter code with excessive `None` checks or fallback defaults for critical configurations. Raise explicit errors for missing or invalid configuration.
- **Refactor over Duplicate**: Do not duplicate logic to avoid touching existing code. Refactor the existing code to support the new requirement.

```python
# Bad — speculative fallback
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY") or os.getenv("API_KEY")

# Good — single canonical name, fail fast
api_key = os.environ["OPENAI_API_KEY"]  # KeyError if missing
```

## 3. Structure & Responsibilities

- Keep domain boundaries clear: `core/` for reusable logic, `modules/*` for API-facing features, `db/` for persistence, `static/` for dashboard assets.
- Follow module layout conventions in `app/modules/<feature>/`: `api.py` (routes), `service.py` (business logic), `repository.py` (DB access), `schemas.py` (Pydantic I/O models).
- Prefer small, focused files; split when a file grows beyond a single responsibility or mixes layers.
- Avoid god-classes: a class should have one reason to change and a narrow public surface.
- Functions should be single-purpose and side-effect aware; separate pure transformations from I/O.
- Do not mix API schema construction with persistence/query logic; map data in service layer.
- Validate inputs early and fail fast with clear errors; never silently coerce invalid types.

```
app/
├── core/           # Reusable logic (auth, config, errors)
├── db/             # Database models, session, migrations
├── modules/
│   └── <feature>/
│       ├── __init__.py
│       ├── api.py          # FastAPI routes
│       ├── service.py      # Business logic
│       ├── repository.py   # DB access
│       └── schemas.py      # Pydantic I/O models
├── dependencies.py # DI context providers
├── main.py         # App entry
└── static/         # Dashboard assets
```

## 4. Testing / TC

- Add or update tests whenever contracts change (field names/types, response formats, default values).
- Keep unit tests under `tests/unit` and integration tests under `tests/integration` using existing markers.
- Tests should assert public behavior (API responses, service outputs) rather than internal implementation details.
- Use fixtures for DB/session setup; do not introduce network calls outside the test server stubs.
- Prefer deterministic inputs (fixed timestamps, explicit payloads) to avoid flaky tests.

```python
# Fixture pattern
@pytest.fixture
async def session():
    async with async_session_maker() as session:
        yield session
        await session.rollback()

# Assert public behavior, not internals
async def test_create_key(client: AsyncClient):
    resp = await client.post("/api/keys", json={"name": "test"})
    assert resp.status_code == 201
    assert "key" in resp.json()
```

## 5. DI & Context

- Use FastAPI `Depends` providers in `app/dependencies.py` to construct per-request contexts (`*Context` dataclasses).
- Contexts should hold only the session, repositories, and service for a single module; avoid cross-module service coupling.
- Repositories must be constructed with the request-scoped `AsyncSession` from `get_session`; no global sessions.
- Services should be instantiated inside context providers and receive repositories via constructor injection.
- Background tasks or standalone scripts must create and manage their own session; do not reuse request contexts.
- When adding a new module, define `api.py` endpoints that depend on a module-specific context provider.

```python
# Template: app/dependencies.py
@dataclass
class FeatureContext:
    session: AsyncSession
    repository: FeatureRepository
    service: FeatureService

async def get_feature_context(
    session: AsyncSession = Depends(get_session),
) -> FeatureContext:
    repo = FeatureRepository(session)
    service = FeatureService(repo)
    return FeatureContext(session=session, repository=repo, service=service)
```

## 6. Module Layout Template

Follow this structure when adding a new module:

```python
# app/modules/<feature>/api.py
from fastapi import APIRouter, Depends
from app.dependencies import get_feature_context, FeatureContext

router = APIRouter(prefix="/<feature>", tags=["<feature>"])

@router.get("/")
async def list_items(ctx: FeatureContext = Depends(get_feature_context)):
    return await ctx.service.list_items()

# app/modules/<feature>/service.py
class FeatureService:
    def __init__(self, repository: FeatureRepository) -> None:
        self._repo = repository

    async def list_items(self) -> list[FeatureResponse]:
        items = await self._repo.get_all()
        return [FeatureResponse.model_validate(item) for item in items]

# app/modules/<feature>/repository.py
class FeatureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> Sequence[FeatureModel]:
        result = await self._session.execute(select(FeatureModel))
        return result.scalars().all()

# app/modules/<feature>/schemas.py
class FeatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    created_at: datetime
```

## 7. DB Migration Governance

- Alembic revision IDs must match `^\d{8}_\d{6}_[a-z0-9_]+$` and migration filenames must be `<revision>.py`.
- Never edit or reorder an already merged migration file. Add a new forward-only migration to correct behavior.
- If multiple heads are created in parallel branches, add an explicit Alembic merge revision before merge/release so CI sees a single head.
- Keep `alembic_version` compatibility during cutovers by remapping legacy revision IDs through application migration tooling; do not patch production tables by hand unless a runbook explicitly requires it.
- Local verification order for migration changes is: `codex-lb-db upgrade head` -> `codex-lb-db check` -> relevant `pytest` suites.
