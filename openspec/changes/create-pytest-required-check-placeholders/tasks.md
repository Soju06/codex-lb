## 1. CI workflow

- [x] 1.1 Remove the job-level backend path filter from the pytest matrix job so
      GitHub creates all required pytest matrix check contexts.
- [x] 1.2 Add a cheap successful placeholder step for non-backend changes.
- [x] 1.3 Keep checkout, dependency setup, and real pytest execution gated on
      backend changes.

## 2. Verification

- [x] 2.1 Add regression tests for the workflow shape.
- [ ] 2.2 Run focused unit tests for the workflow regression coverage.
- [ ] 2.3 Validate the OpenSpec change strictly.
- [ ] 2.4 Confirm GitHub CI and Codex review on the PR head.
