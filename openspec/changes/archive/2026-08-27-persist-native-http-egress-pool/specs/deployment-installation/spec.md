## MODIFIED Requirements

### Requirement: Official Linux container packages locked native egress

The official Linux container build MUST compile the committed native egress lockfile in an isolated Rust build stage and MUST install only the resulting release executable as `codex-lb-native-egress` on the runtime path. The executable MUST support a long-lived multiplexed request protocol and reusable reqwest client pools without requiring a sidecar or operator setting. The runtime image MUST NOT contain the Rust toolchain or Cargo build directory. Python wheel and source installs MUST remain valid when the executable is absent.

#### Scenario: Container runtime exposes native helper

- **WHEN** the official Linux image is built from the repository
- **THEN** `codex-lb-native-egress` is executable on the runtime path
- **AND** it was built with the committed lockfile
- **AND** it accepts multiple request commands during one process lifetime
- **AND** Cargo and the Rust compiler are absent from the runtime image

#### Scenario: Universal Python package remains portable

- **WHEN** a wheel or source install runs on a platform without the helper
- **THEN** importing and starting codex-lb succeeds
- **AND** supported direct requests fall back to the Python transport
