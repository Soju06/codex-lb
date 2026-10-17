# Verification

Base: `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.

## Scope and overlap

The sole production file is `app/modules/health/api.py`. The existing raw socket
peer helper feeds one guard for the three internal drain endpoints. PR #2344
changes drain status observations, #2506 changes scheduler shutdown and #2630
bounds HTTP client closure; none implements this authorization boundary. The
canonical requirement is placed near the existing drain-barrier contract to
avoid colliding with the separate HTTP-close requirement appended by #2630.

## Correctness and compatibility

On the unchanged base, 12 new boundary cases fail and the three ordinary local
controls pass. On the candidate, this affected suite reports 125 passed:

```sh
uv run pytest -q tests/integration/test_internal_drain_access.py tests/integration/test_health_probes.py tests/unit/test_health_probes.py tests/unit/test_graceful_shutdown.py tests/unit/test_prestop.py
```

The regressions exercise IPv4/IPv6 remote peers with forwarding projection,
all three endpoints without capture, valid and malformed drain deadlines, and
no state or bridge side effect on rejection. Direct loopback callers, IPv6 and
mapped loopback addresses retain current address semantics. Existing reversible
drain, committed shutdown, bridge status and preStop coverage pass.

Independent review of `dd4a795f0` found no concrete bypass or regression and
passed 76 additional related checks. The boundary is the supported launcher's
captured peer. A local reverse proxy remains local; this does not introduce
a second policy for requests arriving through it.

Ruff, focused type checks, and strict change/canonical-spec validation pass.
Global strict validation has the same 17 failing specifications and identical
issue data as the unchanged baseline.

## Full local gate

`uv run pre-commit run local-ci --hook-stage manual --all-files` ran with
isolated SQLite and PostgreSQL databases. Frontend checks/build, Python lint
and type checks, Rust checks/build and dependency policy passed. Unit/simulation
reported **10480 passed, 1 failed, 99 skipped, 1 xfailed**. The sole failure was
the unchanged macOS temporary-filesystem guard test already reproduced on this
exact baseline. Later stages were not reached; full local CI is not claimed
green. GitHub CI is checked separately after publication.
