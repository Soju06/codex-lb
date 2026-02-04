# AGENTS

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)

## Code Conventions (Typing & Data Contracts)

- Prefer strict typing end-to-end. Avoid `dict`, `Mapping[str, object]`, and `object` in app/service/repository layers when the shape is known.
- Use explicit dataclasses or Pydantic models for internal payloads; convert to response schemas at the edge.
- ORM models should be passed through services instead of generic containers; avoid `getattr`/`[]` access on ORM results.
- Expose time values in dashboard APIs as ISO 8601 strings (`datetime` in schemas), not epoch numbers.
- If a test depends on a contract change (field name/type), update the test to match the new typed schema.

## Code Conventions (Anti-Patterns to Avoid)

- **No Speculative Fallbacks**: Do not use multiple keys for the same configuration (e.g., `os.getenv("A") or os.getenv("B")`). Pick one canonical name and stick to it.
- **Single Source of Truth**: Do not create redundant fields in data models (JSON/DB) that represent the same state. Calculate derived values dynamically.
- **Fail Fast**: Do not clutter code with excessive `None` checks or fallback defaults for critical configurations. Raise explicit errors for missing or invalid configuration.
- **Refactor over Duplicate**: Do not duplicate logic to avoid touching existing code. Refactor the existing code to support the new requirement.

## Code Conventions (Structure & Responsibilities)

- Keep domain boundaries clear: `core/` for reusable logic, `modules/*` for API-facing features, `db/` for persistence, `static/` for dashboard assets.
- Follow module layout conventions in `app/modules/<feature>/`: `api.py` (routes), `service.py` (business logic), `repository.py` (DB access), `schemas.py` (Pydantic I/O models).
- Prefer small, focused files; split when a file grows beyond a single responsibility or mixes layers.
- Avoid god-classes: a class should have one reason to change and a narrow public surface.
- Functions should be single-purpose and side-effect aware; separate pure transformations from I/O.
- Do not mix API schema construction with persistence/query logic; map data in service layer.
- Validate inputs early and fail fast with clear errors; never silently coerce invalid types.

## Code Conventions (Testing / TC)

- Add or update tests whenever contracts change (field names/types, response formats, default values).
- Keep unit tests under `tests/unit` and integration tests under `tests/integration` using existing markers.
- Tests should assert public behavior (API responses, service outputs) rather than internal implementation details.
- Use fixtures for DB/session setup; do not introduce network calls outside the test server stubs.
- Prefer deterministic inputs (fixed timestamps, explicit payloads) to avoid flaky tests.

## Code Conventions (DI & Context)

- Use FastAPI `Depends` providers in `app/dependencies.py` to construct per-request contexts (`*Context` dataclasses).
- Contexts should hold only the session, repositories, and service for a single module; avoid cross-module service coupling.
- Repositories must be constructed with the request-scoped `AsyncSession` from `get_session`; no global sessions.
- Services should be instantiated inside context providers and receive repositories via constructor injection.
- Background tasks or standalone scripts must create and manage their own session; do not reuse request contexts.
- When adding a new module, define `api.py` endpoints that depend on a module-specific context provider.

## OpenSpec Best Practices

- Use the OpenSpec workflow for non-trivial changes: explore, define change artifacts, implement, verify, archive.
- Keep change artifacts scoped, minimal, and stored under `openspec/`; avoid duplicating or forking specs.
- Sync delta specs back to main specs after implementation and update tests/contracts to match.

## OpenSpec Documentation Model (Spec + Context)

- `spec.md` is the normative SSOT and should contain only testable requirements.
- Use `openspec/specs/<capability>/context.md` for free-form context (purpose, rationale, examples, ops notes).
- If context grows, split into `overview.md`, `rationale.md`, `examples.md`, or `ops.md` within the same capability folder.
- Change-level notes live in `openspec/changes/<change>/context.md` or `notes.md`, then sync stable context back into the main context docs.
- Use the `openspec-context-docs` skill when writing or updating OpenSpec documentation, context docs, or onboarding/guide docs.

Prompting cue:
"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

## Git Workflow & Contribution

1. **Important**: Create branches, commits, or PRs **only upon explicit user request**. Implicit actions are not allowed.
2. **Branch Naming**: Use prefixes like `feature/`, `fix/`, `chore/` (e.g., `feature/add-login`).
3. **Commit Messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/).
   - Format: `<type>(<scope>): <description>`
   - Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`
   - Example: `feat(api): add auth endpoint`
4. **Workflow**:

   ```bash
   git checkout -b feature/add-login
   git commit -m "feat(api): add auth endpoint"
   # Only on explicit request:
   git push -u origin feature/add-login
   gh pr create --title "feat(api): add auth" --body "..."
   ```

5. **Best Practices**: Commit often in small units. Do not commit directly to `main`. Always check `git diff` before pushing.
