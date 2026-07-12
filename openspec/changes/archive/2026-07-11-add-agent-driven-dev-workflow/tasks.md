# Add Agent-Driven Development Workflow — Tasks

Top-level checklist. The full step-by-step plan with code lives in
`implementation-plan.md` (same folder).

- [ ] **Task 1**: Bootstrap OpenSpec change folder (proposal.md, tasks.md,
      spec.md, README.md, implementation-plan.md)
- [ ] **Task 2**: Add `jsonschema` to test deps; update lockfile
- [ ] **Task 3**: Write JSON Schema for contracts
- [ ] **Task 4**: Write validator CLI `openspec/process/scripts/validate_contracts.py`
- [ ] **Task 5**: Write unit tests that validate the five contracts against the schema
- [ ] **Task 6**: Write `feature.yaml` contract
- [ ] **Task 7**: Write `bugfix.yaml` contract
- [ ] **Task 8**: Write `release-beta.yaml` contract
- [ ] **Task 9**: Write `release-stable.yaml` contract
- [ ] **Task 10**: Write `sync-upstream.yaml` contract
- [ ] **Task 11**: Write `process-map.md` (cheat sheet with mermaid)
- [ ] **Task 12**: Write `release-log.md` template + `contracts/README.md`
- [ ] **Task 13**: Write `/process` SKILL.md
- [ ] **Task 14**: Write `process-check.yml` GitHub Action
- [ ] **Task 15**: Add `process-check` job hook to existing `ci.yml`
- [ ] **Task 16**: Add integration test for `process-check.yml`
- [ ] **Task 17**: Update `CLAUDE.md` with pointer to process-map.md
- [ ] **Task 18**: Update `.github/CONTRIBUTING.md` with `/process` mention
- [ ] **Task 19**: Final validation: `uv run pytest`, `uv run ruff`, `openspec validate`
- [ ] **Task 20**: Run `openspec sync` to merge delta specs into main specs