## 1. Timeout invariant policy

- [x] 1.1 Verify curated timeout inequalities against current code before
  encoding them.
- [x] 1.2 Add a declarative rule table with the accepted 8 verified startup
  inequalities and code-anchored rationales.
- [x] 1.3 Leave unverified curated timeout entries as TODOs rather than
  enforcing them.

## 2. Runtime and CI validation

- [x] 2.1 Validate effective settings during application startup.
- [x] 2.2 Keep startup non-strict by default and log CRITICAL for violations.
- [x] 2.3 Add strict mode that raises on violations.
- [x] 2.4 Add a runnable CI entrypoint that validates settings strictly and
  exits nonzero on violations.

## 3. Regression coverage

- [x] 3.1 Prove defaults satisfy all enforced rules.
- [x] 3.2 Prove an inverted configuration names the specific violated rule.
- [x] 3.3 Prove strict mode raises.
- [x] 3.4 Run focused tests and OpenSpec validation before commit.
