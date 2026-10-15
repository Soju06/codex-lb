## Context

See proposal.md. The benchmark already pins both app database routes before imports and runs each migration in a separate subprocess.

## Goals / Non-Goals

Keep explicit target selection and preserve measurements after successful and failed attempts. Leave fixture generation, graph traversal and transaction boundaries unchanged.

## Decisions

Use required `--db-url-env NAME` rather than an implicit application environment default. This avoids silently choosing an inherited live target and adds no runtime setting. Read the selected value before app imports, then pin both existing database variables. The argument contains only an environment variable name.

Store resulting revisions within each step after the CLI returns. Keep the top-level stamps as the final observed database state. JSON and the embedded JSON in Markdown retain both.

## Risks / Trade-offs

Environment variables remain accessible to sufficiently privileged processes. Operators must provision the selected variable without putting credentials in shell history and must keep the target disposable. This removes argument exposure, not all host-level secret access.

## Migration plan

Update the unpublished command's usage and subprocess tests together. Existing archived measurement reports remain immutable; their timing proof is still applicable to unchanged migration and fixture code.
