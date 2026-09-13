## 1. Dependency Floor

- [x] 1.1 Declare `anyio>=4.14.0` in `pyproject.toml` and refresh `uv.lock` (anyio 4.15.1).

## 2. Regression Coverage

- [x] 2.1 Add a deterministic unit test that reproduces the release-with-cancelled-waiter interleaving for `anyio.Lock`, `anyio.Semaphore`, and the stdlib `asyncio.Lock` reference and asserts the newcomer acquires.
- [x] 2.2 Confirm the test fails against anyio 4.13.0 and passes against the locked version.

## 3. Validation

- [x] 3.1 Run the focused unit test, lint, type, and architecture checks.
- [x] 3.2 Run strict change-local OpenSpec validation.
