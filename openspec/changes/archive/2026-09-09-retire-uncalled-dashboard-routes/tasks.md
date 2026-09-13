# Tasks

- [x] 1.1 Audit every uncalled dashboard route across docs, OpenSpec specs and
      active changes, frontend, scripts, deploy and tests; record the consumer
      evidence and apply the owner's removal rule.
- [x] 1.2 Remove the deprecated `POST /{account_id}/export` and
      `POST /{account_id}/export/opencode-auth` handlers, response models and
      service methods; keep `POST /export/auth`.
- [x] 1.3 Remove `DELETE /api/sticky-sessions/{kind}/{key}`, its response model
      and `delete_entry`; keep the repository `delete()` used by proxy routing.
- [x] 1.4 Remove `GET /api/conversation-archive/files`, its response model and
      the file-listing service helpers.
- [x] 1.5 Redirect tests to the surviving routes (unified export, batch delete,
      `/records`), fold OpenCode export scenarios into the unified export test,
      and assert the retired export routes respond 404.
- [x] 1.6 Drop the MSW mock, handler-coverage entry and unused archive-file
      schema in the frontend.
- [x] 1.7 Run `make lint`, `uv run ty check`, focused unit and integration
      tests, frontend tests and `openspec validate`.
