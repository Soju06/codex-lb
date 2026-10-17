## Context

The project is a Python 3.13 Hatch application whose runtime, optional, and development dependencies are already resolved in `uv.lock`. The distribution exposes `codex-lb` and `codex-lb-db`, and includes static dashboard assets in its wheel. See `proposal.md` for motivation and `specs/deployment-installation/spec.md` for the observable contract.

## Goals / Non-Goals

**Goals:**

- Keep the Nix dependency graph aligned with the committed uv resolution instead of maintaining a second hand-written dependency list.
- Provide conventional `nix build`, `nix run`, `nix develop`, and `nix flake check` behavior on Linux and macOS systems supported by the flake inputs.
- Make development editable while keeping package builds filtered, immutable, and reproducible.
- Expose a normal application derivation rather than presenting a Python virtual environment as the package interface.

**Non-Goals:**

- Add a NixOS module or background service definition.
- Replace uv for non-Nix development or change existing Docker, Compose, or Helm paths.
- Enable optional runtime metrics or tracing extras in the default proxy package.
- Submit codex-lb to nixpkgs.

## Decisions

### Build from `uv.lock` with uv2nix and pyproject.nix

Use the upstream uv2nix workspace loader, pyproject.nix package builder, and build-system overlay, with all flake inputs following one pinned nixpkgs input. This directly consumes project metadata and hashes, supports editable local packages, and avoids duplicating every Python dependency in Nix. A handwritten `buildPythonApplication` derivation was rejected because it would create a second dependency-resolution source of truth and require recurring version mapping to nixpkgs.

Prefer compatible wheels from the lock because the application includes several native Python dependencies and uv2nix documents wheels as the most reliable default. Build-system support remains available for the local Hatch package and dependencies that require source builds.

Hatch loads its `editables` helper only when building a PEP 660 editable wheel, so uv does not include that dynamic build requirement in the application lock. Supply `editables` explicitly from the pinned build-system overlay as a native build input; it affects only the build environment and does not enter the runtime closure.

### Package the application with `mkApplication`

Build the locked runtime virtual environment, then use pyproject.nix's `mkApplication` to expose only application-facing package content and wrapped entry points. Returning the virtual environment itself was rejected because it exposes Python environment internals as the distributable package interface.

The package source is filtered with `lib.fileset` to the application packages, metadata, license, and readme. Build the dashboard separately with nixpkgs' Bun package and the committed `frontend/bun.lock`, then copy the compiled output into `app/static` before Hatch builds the Python wheel. Keep the fetched frontend modules in a fixed-output derivation so network access is restricted to the dependency-fetching stage and Bun does not enter the runtime closure. Workspace metadata remains unfiltered at evaluation time, following uv2nix guidance that filtering the workspace root would introduce import-from-derivation behavior.

Keep module-root env-file discovery unchanged and give the packaged entry
points an explicit override instead. The installed package lives in the
immutable Nix store, so module-root discovery can never find an operator's
`.env` there; the package wrapper defaults the `CODEX_LB_ENV_FILE`
settings-load override (an `os.pathsep`-separated path list) to the launch
directory's `.env` and `.env.local`. Rebasing discovery on the process working
directory was rejected because it silently changes env-file selection for
every non-Nix launch mode (for example `uvx codex-lb` run from a directory
containing an unrelated `.env`).

### Use an editable, Nix-managed development environment

Apply uv2nix's editable overlay only to the development Python set and build the shell environment from default runtime dependencies plus the `dev` dependency group. Documentation tooling and optional metrics and tracing integrations remain opt-in. Set `UV_NO_SYNC`, `UV_PYTHON`, and `UV_PYTHON_DOWNLOADS` so uv remains available for lockfile operations without creating a competing environment or downloading an interpreter. The shell resolves its repository root dynamically and clears ambient `PYTHONPATH` to avoid host contamination.

### Expose conventional flake outputs without an output helper framework

Generate outputs with nixpkgs `lib.genAttrs` for AArch64 and x86-64 Linux plus AArch64 Darwin. The pinned nixpkgs revision no longer supports x86-64 Darwin, so the flake does not claim an output that nixpkgs cannot evaluate. Expose `packages.default`, `packages.codex-lb`, `apps.default`, `apps.codex-lb`, `devShells.default`, `checks`, and `formatter`. A separate flake-output framework was rejected because the small, regular output matrix does not justify another abstraction or input.

### Keep flake checks focused on the distributable output

The flake check builds the packaged application. Command and dashboard checks are left to project-level validation rather than duplicated as extra Nix derivations.

## Risks / Trade-offs

- **Locked wheels may not exist for every system declared by nixpkgs** → Limit outputs to the standard Linux and Darwin CPU architectures and let flake checks expose incompatibilities when inputs are updated.
- **Native wheel patching can fail after dependency updates** → Keep nixpkgs, uv2nix, pyproject.nix, and build-system inputs pinned together and verify a clean build after lock updates.
- **The development closure can grow when unrelated dependency sets are enabled** → Keep the default shell to runtime dependencies plus the `dev` group; leave documentation, metrics, and tracing dependencies opt-in.
- **Editable setup depends on locating the checkout at shell entry** → Resolve `REPO_ROOT` with Git when available and fall back to the current directory for unpacked source trees.
- **Launching from the wrong directory can select unintended env files** → Scope launch-directory env-file loading to the Nix wrapper's explicit `CODEX_LB_ENV_FILE` default; every other launch mode keeps module-root discovery, and process environment variables retain precedence over env files.

## Migration Plan

Additive only: commit the flake and lock file, validate the outputs, and document the commands in deployment-installation context. Rollback is removal of the flake files and the corresponding OpenSpec requirement; existing installation modes are unaffected.
