# Design

## Repository-owned orchestration

The version-aware runner remains responsible only for locking, trigger
selection, allocating a run directory, invoking a configured argv, validating
the final result, and atomically advancing state. The configured command calls
a repository module with explicit repo, runner, auth, and approved-root paths.

That module validates the run directory and auth permissions, builds the
native helper, runs raw HTTP/2 and failure suites, evaluates their gates,
removes only enumerated sensitive subtrees, scans retained evidence, and
writes the success marker. A `finally` cleanup runs on every exception.

## Artifact primitives

One small module owns JSON loading, SHA-256/byte-count digests, evidence
attestations, and atomic JSON/text replacement. Gate projections remain in
their domain modules. Atomic helpers create a same-directory temporary file,
flush and fsync it, replace the destination, and remove abandoned temporaries.

## Compatibility

The final `fast-canary.json`, failure baseline, Markdown reports, strict exit
codes, scheduler state schema, scratch layout, and systemd trigger policy stay
compatible. The obsolete host Bash implementation is removed only after the
declarative command passes dry-run and a forced smoke suite.
