## Context

The migration CLI parses `--db-url` with a `None` default, but resolves the target with a truthiness expression. An explicit empty argv value is therefore indistinguishable from omission at target resolution and selects `get_settings().database_url` before any subcommand dispatch. All six subcommands share this path; `upgrade` and `stamp` can durably mutate the unintended database, while the inspection and wait commands can still open or create it.

## Goals / Non-Goals

**Goals:**

- Reject an explicitly supplied exact empty `--db-url` during argument parsing, before settings construction and database I/O.
- Keep omitted `--db-url` behavior unchanged for deployment jobs that rely on settings fallback.
- Cover all six supported subcommands at the real subprocess/CLI seam and prove the fallback target remains untouched on rejection.

**Non-Goals:**

- No whitespace trimming or whitespace-only rejection.
- No validation or normalization of non-empty URL syntax.
- No validation of an empty global database setting.
- No Alembic, Helm, Makefile, settings-model, or dashboard changes.

## Decisions

- Add a narrow `argparse` value converter for the global `--db-url` option that raises `ArgumentTypeError` only when the supplied value is exactly `""`. `argparse` does not call the converter for the `None` default, so omission remains distinguishable and rejection occurs before `get_settings()`.
- Resolve the database target with an explicit `args.db_url is None` branch. A non-empty supplied value remains authoritative and passes through unchanged. This makes the omission contract visible in the code instead of relying on truthiness.
- Add one parameterized real-subprocess regression covering `upgrade`, `current`, `check`, `wait-for-head`, `wait-for-connection`, and `stamp`. Each case uses a fresh configured fallback target, asserts argument-error exit, and asserts no fallback file creation or mutation.
- Add an omitted-option control that upgrades the configured settings target to head. This protects the deployment contract rather than only testing the rejection path.

Alternatives considered:

- Validating after settings resolution was rejected because constructing settings would violate the fail-before-fallback contract and leave side-effect ordering ambiguous.
- Treating whitespace-only values as empty was rejected as scope expansion; whitespace is a supplied non-empty value and retains existing URL parsing behavior.
- Adding global database-setting validation was rejected because it is a separate configuration contract and does not fix the CLI omission collapse at its source.

## Risks / Trade-offs

- The parser error text becomes part of operator-facing CLI output; tests assert argument-error semantics and a stable explanatory fragment without coupling to the entire usage banner.
- Running all six commands as subprocesses costs more than a helper unit test, but it is required to prove the real argv and database-side-effect boundary.
- The explicit `is None` branch is intentionally redundant with parser validation. It preserves the target-resolution invariant if validation changes later.
