## ADDED Requirements

### Requirement: Nix flake provides reproducible development and execution paths

The repository MUST provide a locked Nix flake for each supported Nix platform. The flake MUST expose the proxy as its default package and default app, and MUST expose a default development shell containing an editable project installation, all locked runtime dependencies, the `dev` dependency group, and the project package manager. The default package and development shell MUST exclude documentation dependencies and optional runtime integrations unless they are required by those outputs. The flake package and development shell MUST use Python 3.13 and MUST derive Python dependencies from the committed `pyproject.toml` and `uv.lock` files.

#### Scenario: Default package builds the proxy

- **WHEN** a user runs `nix build`
- **THEN** Nix builds a package containing the `codex-lb` and `codex-lb-db` commands
- **AND** the package contains the compiled dashboard served by the proxy root route
- **AND** the package uses the dependency versions and hashes recorded by the flake and Python lock files

#### Scenario: Default app runs the proxy CLI

- **WHEN** a user runs `nix run . -- --help`
- **THEN** the packaged `codex-lb` command prints its CLI help and exits successfully
- **AND** running `nix run .` without help arguments starts the proxy through the project-owned CLI entry point

#### Scenario: Development shell is editable and complete

- **WHEN** a user enters the repository with `nix develop`
- **THEN** the shell provides Python 3.13, `uv`, the project CLI entry points, runtime dependencies, and the `dev` dependency group
- **AND** Python imports resolve the project packages from the working tree so source edits take effect without rebuilding the shell
- **AND** documentation dependencies and optional metrics and tracing integrations are absent from the default shell
- **AND** `uv` is prevented from downloading Python or replacing the Nix-managed environment

#### Scenario: Flake check builds the package

- **WHEN** a user runs `nix flake check`
- **THEN** Nix builds the default package
