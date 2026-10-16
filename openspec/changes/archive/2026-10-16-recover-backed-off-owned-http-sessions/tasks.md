## 1. Reproduce and implement

- [x] 1.1 Add real-selection HTTP route regressions for anchor denial, transient backoff, a successful sibling session, and same-owner full resend; verify failure on unmodified main.
- [x] 1.2 Integrate the attributed #2486 hard-owner-pool implementation and its selector regressions; verify the HTTP recovery cases pass without changing replay or ownership gates.
- [x] 1.3 Verify quota/status refusal, unchanged encrypted/tool history, subsequent turn state, and retained selection health evidence using route and selector tests.

## 2. Validate and submit

- [x] 2.1 Run affected selection and transport suites, lint, type checking, and architecture guards; compare any environmental failures against the baseline.
- [x] 2.2 Strictly validate and sync OpenSpec requirements/context, verify coverage, then archive the completed change.
- [x] 2.3 Prepare a separate focused PR branch and description with #2486 attribution and overlap/dependency notes, leaving #2626 unchanged.
