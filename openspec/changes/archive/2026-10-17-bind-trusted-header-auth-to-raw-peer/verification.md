# Verification

Base: `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.

## Scope and related work

The production diff changes only trusted-header source selection in the
sanitizer and principal resolver. The existing captured socket peer is reused.
PR #1380 supplies the capture/projection boundary, #1987 applies it to the API
firewall, and #2010 handles identity cardinality; those merged changes do not
replace trusted-header source selection. No overlapping open implementation was
found in the bounded issue/PR search.

## Correctness and compatibility

Seven new regression cases fail on the unmodified base. With this change, the
affected command below passes 81 tests. It covers real application middleware
with trusted/untrusted IPv4 and IPv6 transport peers, source-header removal,
missing capture, authenticated session creation, and preserved forwarded client
and scheme. Existing identity cardinality, role mapping and password fallback
remain covered.

```sh
uv run pytest -q tests/integration/test_auth_middleware.py tests/unit/test_dashboard_auth_mode.py tests/integration/test_trusted_header_identity.py tests/integration/test_role_mapping_reevaluation.py tests/unit/test_dashboard_auth_dependencies.py tests/unit/test_trusted_proxy_headers_middleware.py
```

Tests used isolated SQLite storage. A subsequently completed fixture field was
verified by rerunning the two affected unit cases. Focused Ruff/type checks and
strict validation of the change and canonical admin-auth specification pass.

Independent review of `2ef6487b0` found no concrete bypass or compatibility
regression. The reviewer also passed 72 related auth/provider/step-up tests and
378 in-memory boundary cases. The supported launch path captures the peer before
projection; an external ASGI embedding that projects before capture is outside
that launch contract.

## Full local gate

`uv run pre-commit run local-ci --hook-stage manual --all-files` was run
with isolated SQLite and PostgreSQL databases. Frontend lint, typecheck, tests
and build; Python lint/typecheck; Rust checks/build and dependency policy passed.
The unit/simulation stage reported **10482 passed, 1 failed, 99 skipped, 1 xfailed**.
Its sole failure was the unchanged macOS temporary-filesystem guard test,
`test_output_under_a_temporary_filesystem_is_refused`, already reproduced on
this exact unmodified base. Later gate stages were not reached. This is not
a claim that the full local gate is green.

Global strict OpenSpec validation retains the same 17 pre-existing failing
specifications and identical issue data as the base. The changed specification
and this change both pass strict validation.
