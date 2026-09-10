# Design

## Final-state repository layout

The repository root is a virtual Cargo workspace and `crates/` is the stable
home for Rust production code. This layout works both during the hybrid period
and after the Python backend is retired, avoiding a later `rust/` subtree move.
The single root lockfile reflects that codex-lb ships applications rather than
independently versioned libraries.

## Dependency layers

`codex-lb-protocol` owns serializable IPC types and has no runtime or network
dependencies. `codex-lb-egress` owns reusable transports and depends on the
protocol. `codex-lb-egress-worker` is a thin binary shell over the library.
Future in-process Rust application code can therefore reuse egress without
depending on stdio or a subprocess executable.

## Compatibility and rollout safety

Python sends a bounded version range before assigning a helper generation.
Rust selects its current version and returns capabilities. Python validates all
required capabilities before starting the multiplexed reader. Missing binaries
retain the existing pre-dispatch fallback, while installed-but-incompatible
binaries fail closed so package skew cannot create an unsafe replay.

## Policy gates

The toolchain, formatting, Clippy, tests, and release build are deterministic
workspace gates. cargo-deny additionally checks advisories, licenses, wildcard
dependencies, and registry/Git provenance; the two intentional OpenAI fork
sources are explicit allowlist entries.
