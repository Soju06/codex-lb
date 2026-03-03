## 1. OpenSpec and Governance Updates

- [x] 1.1 Create OpenSpec change artifacts (proposal/design/specs/tasks)
- [x] 1.2 Update `openspec/specs/database-migrations/spec.md` requirements for revision policy and remap behavior
- [x] 1.3 Add `openspec/specs/database-migrations/context.md` with rollout/ops guidance
- [x] 1.4 Add DB migration governance section to `.agents/skills/project-conventions/conventions.md`

## 2. Revision Rewrite and Runtime Support

- [x] 2.1 Add a single-source revision mapping module for old->new IDs
- [x] 2.2 Add one-shot rewrite script with `--dry-run` and `--apply`
- [x] 2.3 Rewrite Alembic version filenames and revision/down_revision/docstring IDs to timestamp format
- [x] 2.4 Implement startup auto-remap of legacy Alembic IDs before upgrade

## 3. Check Command, CI, and Verification

- [x] 3.1 Extend migration check command to validate head count and revision naming/filename policy
- [x] 3.2 Keep drift validation in the same check path and fail on any violation
- [x] 3.3 Update CI migration-check job to use unified CLI check path
- [x] 3.4 Update/add unit and integration tests for new revision IDs and auto-remap behavior
