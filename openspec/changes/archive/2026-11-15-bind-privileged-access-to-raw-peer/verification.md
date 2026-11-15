## Verification — 2026-11-15

- Affected authentication, role mapping, trusted-proxy projection, health, drain, shutdown and preStop selection: **206 passed**, one existing Starlette/AnyIO deprecation warning, 55.92 seconds.
- Command: `python -m pytest tests/integration/test_auth_middleware.py tests/unit/test_dashboard_auth_mode.py tests/integration/test_trusted_header_identity.py tests/integration/test_role_mapping_reevaluation.py tests/unit/test_dashboard_auth_dependencies.py tests/unit/test_trusted_proxy_headers_middleware.py tests/integration/test_internal_drain_access.py tests/integration/test_health_probes.py tests/unit/test_health_probes.py tests/unit/test_graceful_shutdown.py tests/unit/test_prestop.py -q --show-capture=no`.
- Baseline reproduction loads only the original three modules' affected methods from Git HEAD into a separate Python process, without changing repository files. The 22 focused new boundary cases produce **19 failed, 3 passed**. Failures independently demonstrate untrusted projected identity acceptance, trusted-proxy rejection, absent-provenance authentication, forwarded drain access and authorization happening after deadline parsing. Ordinary loopback controls pass.
- The composed middleware reproducer now rejects the remote raw peer projecting loopback (no actor, drain 403), while accepting the genuine loopback peer projecting a public client (actor retained, drain 200). Projected attribution remains unchanged.
- Cached offline Ruff check and formatting check pass for all seven changed/new Python files.
- Strict validation of `bind-privileged-access-to-raw-peer`: valid.
- Canonical OpenSpec validation: **67 passed, 0 failed**.
- `git diff --check` for the affected tracked files: clean.

No settings, schemas, dependencies, external network requests or deployment changes are involved. The full cross-stack CI matrix was not run for this focused change. A local reverse proxy remains a loopback transport peer; the existing loopback-only policy does not independently authenticate traffic forwarded by that proxy to internal drain.
