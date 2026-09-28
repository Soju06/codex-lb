## Why

`tests/unit/test_native_egress_packaging.py` asserts on the Rust workspace
manifest, its lockfile, the `crates/**` sources, and both container build
files, but `.github/scripts/detect_changed_areas.py` matched those paths only
in the `rust` area. A pull request that touches them therefore reports
`backend=false`, and the pytest matrix satisfies its required contexts with the
placeholder step instead of running the assertions.

That is not hypothetical. #2453 bumped `Cargo.toml` and `Cargo.lock`; its head
`Tests (pytest, unit)` context reported success after three seconds
(2026-09-18T04:33:39Z -> 04:33:42Z), the merge landed, and the same job then
failed on `main` in push run 35307892544. #2454 had to follow 52 minutes later
to re-pin the test. The gate fired, but only after the merge.

## What Changes

- Add `Cargo.toml`, `Cargo.lock`, `crates/**`, `Dockerfile` and `Dockerfile.*`
  to the `backend` area filter so the real pytest slices run on pull requests
  that change them.
- Cover each of those paths with a regression test on the filter.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: require the `backend` area to match the paths the
  native-egress packaging tests read.

## Impact

Only CI area detection and its unit coverage change. Pull requests that touch
the Rust workspace or the container build files now pay for the backend pytest
slices; no other pull request changes cost. No runtime, configuration, schema
or operator contract changes.

Not addressed here: the backend suite also reads `deploy/**`,
`docker-compose*.yml`, `docs/**`, `openspec/**`, `README.md` and
`.agents/**`, which the `backend` filter still does not match. Adding those
would run the backend slices on nearly every pull request, so that trade-off is
left as a separate maintainer decision rather than folded into this fix.
