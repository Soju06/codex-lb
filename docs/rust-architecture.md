# Rust migration architecture

Normative owner: [Proxy architecture OpenSpec](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/proxy-architecture/spec.md).

codex-lb uses one virtual Cargo workspace at the repository root. Rust source
lives under `crates/`; it is not nested under `rust/` or `native/`. This is the
intended final layout, not a temporary helper layout: when the Python backend is
fully retired, the root workspace and existing crate paths remain in place and
only new application/domain crates and binaries are added.

## Current boundaries

```text
Cargo.toml                         virtual workspace and shared policy
Cargo.lock                        one reproducible application lockfile
rust-toolchain.toml               pinned compiler, rustfmt, and clippy
deny.toml                         advisory, license, and source policy
crates/
  codex-lb-protocol/              versioned Python/Rust IPC data contract
  codex-lb-egress/                reusable HTTP, TLS, and WebSocket transport
  codex-lb-egress-worker/         stdio process lifecycle and binary target
app/core/clients/native_egress.py Python adapter and replay-safe ownership
```

The dependency direction is one way: the worker depends on egress, and egress
depends on protocol. The protocol crate has no async runtime or networking
dependencies. Application policy must not move into the worker merely because
the worker is Rust: Python currently retains account selection, endpoint
ordering, retries, health classification, and persistence.

The worker binary deliberately contains only startup and exit behavior. This
keeps the egress implementation usable from a future in-process Rust server;
the transport will not need to be extracted from a subprocess executable when
that migration reaches the application shell.

## Compatibility contract

Every new helper process completes `client_hello` / `server_hello` negotiation
before receiving a request. The adapter requires protocol version 1 and all
capabilities needed by the current Python call sites. An installed but
incompatible helper fails closed before dispatch; it is not treated as a
missing helper and no ambiguous request is replayed through Python.

`crates/codex-lb-protocol/tests/fixtures/handshake-v1.json` is the shared
cross-language handshake fixture. Wire changes must follow these rules:

1. Add backward-compatible optional fields when possible.
2. Add a capability when behavior, acknowledgement, or failure semantics
   change without requiring a new wire grammar.
3. Increment the protocol version for an incompatible grammar or meaning.
4. Keep old-version fixtures while an independently deployable adapter or
   worker may still use them.
5. Test malformed, missing-capability, cancellation, and process-exit paths;
   success-only compatibility tests are insufficient.

## Adding a migrated slice

Prefer a vertical slice with an explicit boundary over a generic utilities
crate. Domain rules belong in a domain crate, reusable infrastructure in a
focused adapter crate, and executable wiring in an application crate. Shared
types should be promoted only after two real consumers need them.

For each slice:

- Define ownership and retry/cancellation semantics before implementation.
- Keep credential-bearing values out of errors, traces, and IPC diagnostics.
- Expose a narrow library API; do not make other crates depend on a binary.
- Add cross-language contract fixtures while Python remains a consumer.
- Cut over one owner at a time and retain a rollback path only at a proven
  pre-dispatch boundary.
- Remove the Python implementation and IPC surface after the Rust owner is
  proven; do not preserve a permanent dual implementation.

Likely future top-level crates are `codex-lb-domain`, `codex-lb-application`,
focused infrastructure adapters, and a server binary. Those names should be
introduced when their boundaries exist rather than scaffolded empty today.

## Tooling and dependency policy

Run the same checks as CI with:

```bash
make rust-check
```

This checks formatting, Clippy with warnings denied, all workspace tests, and
the release worker build using the committed lockfile. `make rust-audit`
additionally requires `cargo-deny`; CI always runs the pinned cargo-deny action.

Workspace dependencies are declared once in the root manifest. Wire-sensitive
Codex transport dependencies stay exactly pinned, including the audited OpenAI
WebSocket fork revisions. Ordinary dependencies may use compatible ranges, but
every production build uses `Cargo.lock` and `--locked`. New licenses and Git
sources require an explicit `deny.toml` policy change and review.

Unsafe Rust is forbidden workspace-wide. Exceptions, if ever necessary, need
a narrowly scoped crate-level policy, a documented invariant, and targeted
tests rather than weakening the workspace default.
