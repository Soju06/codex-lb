## Why

Nix users currently lack a reproducible project environment and a first-class way to build or run the proxy through flakes. Adding a repository-owned flake makes development and execution follow the same locked Python dependency graph as the rest of the project.

## What Changes

- Add a multi-system Nix flake that builds the `codex-lb` Python application from `pyproject.toml` and `uv.lock`.
- Expose the built proxy as the default package and default flake app so `nix build` and `nix run` work conventionally.
- Add an editable development shell with runtime dependencies, the `dev` dependency group, and repository tooling.
- Add a flake check that builds the packaged application.
- Load `.env` and `.env.local` from the launch directory for the packaged Nix commands via an explicit `CODEX_LB_ENV_FILE` settings-load override defaulted by the package wrapper, so installed commands retain the documented local configuration behavior without changing discovery elsewhere.
- Document the Nix development and run paths in the existing installation context.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Define the reproducible Nix flake package, runnable proxy app, development shell, and validation contract.

## Impact

- Adds root-level `flake.nix` and `flake.lock` files.
- Adds upstream flake inputs for nixpkgs and the pyproject.nix/uv2nix build stack.
- Extends deployment-installation OpenSpec requirements and context with the Nix workflow.
- Adds the `CODEX_LB_ENV_FILE` bootstrap override for env-file locations; the Nix package wrapper defaults it to the launch directory. Env-file discovery for non-Nix launch paths is unchanged.
- Does not change the proxy API or container installation paths.
