## ADDED Requirements

### Requirement: Rust migration uses one final-state workspace

The repository MUST maintain one root virtual Cargo workspace, one committed
application lockfile, a pinned toolchain, and focused production crates below
`crates/`. Protocol, reusable transport, and worker binary MUST remain separate
dependency layers, and CI MUST enforce workspace quality and supply-chain
policy.

#### Scenario: Python backend is eventually retired

- **WHEN** Rust becomes the application owner
- **THEN** the existing root workspace and crates remain at their canonical paths
- **AND** no temporary language subtree needs relocation

## MODIFIED Requirements

### Requirement: Official Linux container packages locked native egress

The official Linux container MUST build the native worker from the root
workspace with the committed lockfile and pinned toolchain, then copy only the
release executable into the runtime image.

#### Scenario: Container runtime exposes native helper

- **WHEN** the official image is built
- **THEN** the helper comes from the locked root workspace
- **AND** the runtime contains neither Cargo nor the Rust compiler
