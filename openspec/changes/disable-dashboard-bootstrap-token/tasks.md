## 1. Specs

- [x] 1.1 Add admin-auth requirements for disabling the remote bootstrap-token gate with `CODEX_LB_DISABLE_BOOTSTRAP_TOKEN`.

## 2. Implementation

- [x] 2.1 Add the new settings flag and wire it into the dashboard auth session and password setup paths.
- [x] 2.2 Wire the new env var through the example env file and Helm configmap.

## 3. Tests

- [x] 3.1 Add integration coverage for remote first-run access with the bootstrap gate disabled.
- [x] 3.2 Add settings coverage for the new boolean env flag.
