## 1. Regression coverage

- [x] 1.1 Add catalog coverage for v1/v2, nonblank future versions, absent/blank/malformed versions, and explicit namespace opt-in.
- [x] 1.2 Add real Responses route coverage for both paths and trailing slashes, nested definitions, matching choices, and mixed supported/unsupported tools.
- [x] 1.3 Confirm the new regression fails before the implementation change.

## 2. Implementation

- [x] 2.1 Derive namespace support from a nonblank string `multi_agent_version` while retaining the existing tool opt-ins.

## 3. Verification and acceptance

- [x] 3.1 Run focused tests, lint, formatting, and type checks.
- [x] 3.2 Validate the modified OpenSpec contract strictly.
- [ ] 3.3 Obtain maintainer agreement on the namespace compatibility contract.
- [x] 3.4 Demonstrate a real Codex parent/child session through a configured Model Source.
- [ ] 3.5 After agreement, synchronize the modified requirement and context into the owning spec and archive the verified change.
