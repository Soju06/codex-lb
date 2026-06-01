## 1. Environment variable override

- [x] 1.1 Read `CODEX_LB_DATA_DIR` inside `_default_home_dir()`.
- [x] 1.2 Return `Path(env_dir)` when the variable is non-empty.

## 2. Existing home-directory detection

- [x] 2.1 Check whether `Path.home() / ".codex-lb"` exists before deciding on the
  container path.
- [x] 2.2 Return the home path when it already exists, regardless of container
  detection.

## 3. OpenSpec

- [x] 3.1 Add a parsed OpenSpec requirement delta for data directory precedence,
  related default paths, and explicit related-path overrides.

## 4. Verification

- [x] 4.1 Add unit tests covering all precedence combinations:
  - `CODEX_LB_DATA_DIR` set → used.
  - `CODEX_LB_DATA_DIR` unset, `~/.codex-lb` exists → used.
  - Neither set, in container → `/var/lib/codex-lb`.
  - Neither set, not in container → `~/.codex-lb` (even if absent).
- [x] 4.2 Run focused tests and lint.
