## 1. Flake Outputs

- [x] 1.1 Add the pinned uv2nix/pyproject.nix flake inputs and per-system Python package set, and verify `nix flake show` evaluates all declared outputs.
- [x] 1.2 Expose a filtered `codex-lb` application package plus default and named apps, build the dashboard from its Bun lockfile, and verify the packaged application runs successfully.
- [x] 1.3 Add the editable development shell with runtime dependencies, the `dev` dependency group, and Nix-managed uv settings; verify source imports, Python 3.13, uv, and development tools inside `nix develop`.
- [x] 1.4 Add formatter and package-build checks, generate `flake.lock`, and verify `nix flake check` succeeds.
- [x] 1.5 Default the packaged entry points' `CODEX_LB_ENV_FILE` override to the launch directory's `.env` and `.env.local`, keeping module-root discovery for non-Nix launch paths, and pin both behaviors with unit tests.

## 2. Documentation and Validation

- [x] 2.1 Add purpose, rationale, constraints, failure modes, and concrete Nix command examples to deployment-installation context, then verify the context links to its owning spec.
- [x] 2.2 Validate the OpenSpec change strictly and inspect the final diff for formatting and unintended files.
