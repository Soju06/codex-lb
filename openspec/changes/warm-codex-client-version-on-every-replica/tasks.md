# Tasks

- [x] 1.1 `_warm_codex_version_cache()` in the model refresh scheduler; `_run_loop` awaits it before `_refresh_once` on every replica; failures are logged, never raised.
- [x] 1.2 Bump `model_registry_client_version` default to `0.153.4`; regenerate `docs/reference/settings.md`.
- [x] 1.3 Tests: non-leader loop tick warms the cache and still reconciles; a failing warm-up still reconciles; codex version fallback tests follow the new default; product path: after the warm-up the real shared cache feeds the non-native fingerprint (`User-Agent` + `version`) instead of the fallback (local codex review P2).
- [x] 1.4 ruff, ty, strict OpenSpec validation.
