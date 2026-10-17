# Candidate verification

Base: `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.

The two entrypoint scripts preserve their existing application command, migration bypass and always-migrate distinctions. No migration SQL, schema, timeout, dependency or deployment configuration changes are included.

The real entrypoint regressions failed on unchanged base for all four combinations of SIGTERM/SIGINT and shell/distroless launchers. With this change, 29 focused tests pass, covering successful handoff, explicit migration enablement, shell bypass, distroless always-migrate, positive failure status, native child signal status, signal forwarding, child reaping, interrupted-start suppression, spawn-time signals, and the exit-observation race.

`make lint`, `make typecheck`, shell syntax validation, and strict validation of the change and its two owning canonical specs pass. The complete strict spec check retains exactly the base's 17 pre-existing invalid capabilities and identical ERROR diagnostics.

Overlap review: PR #2307 adds revision progress logs in the Alembic runner; it does not change either entrypoint or signal supervision. Previously merged PR #1443 drains audit/fleet tasks after the server starts, and #2030 concerns SQLite connection teardown; neither owns the startup-migration process boundary.

An independent review of commit `f0ce879b17e73407f6d4aa3655ea531f1dcf1b19` against the base found no actionable issues, including the spawn window, first-signal status, exit-observation ownership, and process regressions.

Cloud review identified a race in the cleanup regression's immediate PID-exit
assertion after force-killing the process group. The test now uses its existing
bounded PID-exit wait, allowing the operating system to reap the orphaned child.
The same focused command passed all 29 tests again; affected Ruff and type
checks passed. No production behavior changed in this follow-up.

The required `uv run pre-commit run local-ci --hook-stage manual --all-files` was run once using isolated test databases. Frontend lint, typecheck, build, and 1,658 tests passed; Python lint and typecheck passed; Rust formatting, Clippy, 28 tests, release build, and dependency policy checks passed. The unit/simulation stage finished with 10,483 passed, 99 skipped, 1 xfailed, and one failure: `tests/unit/test_codex_body_capture_guards.py::test_output_under_a_temporary_filesystem_is_refused` did not raise `CaptureRefusal`. This unchanged test also fails on the base on this host. The gate exited nonzero at that stage, so the later integration, PostgreSQL, migration, packaging, Docker, and Helm stages were not reached. No tests were excluded or weakened. Cloud CI remains pending publication.
