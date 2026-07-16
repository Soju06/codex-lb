# Tasks: label-sync-app-token

## 1. Workflow

- [x] 1.1 Add a SHA-pinned `actions/create-github-app-token` mint step to both
      `sync-pr` and `sync-after-ci`, gated on the `CODEX_LABEL_SYNC_APP_ID`
      repository variable and marked `continue-on-error` so a failed mint
      falls back instead of failing the job
- [x] 1.2 Prefer the minted token at the head of the `GH_TOKEN` chain in both
      sync steps, keeping `CODEX_LABEL_SYNC_TOKEN` → `RELEASE_PLEASE_TOKEN` →
      `github.token` and the `GH_FALLBACK_TOKEN` escape hatch unchanged

## 2. Spec

- [x] 2.1 Extend the `github-automation` write-token fallback requirement with
      the App-token preference and mint-failure fallback scenario

## 3. Validation

- [x] 3.1 `openspec validate label-sync-app-token --strict`
- [x] 3.2 `actionlint` on the workflow (or CI equivalent)
