## 1. Redaction

- [x] 1.1 Add a fail-closed authorization pattern for non-Bearer, non-Basic values (end of line, optional closing double quote after the key)
- [x] 1.2 Apply it after the Basic pass and before the existing authorization pattern

## 2. Regression coverage

- [x] 2.1 Digest, AWS SigV4, and quoted-key Digest inputs are redacted without leaking later members
- [x] 2.2 Text and JSON formatters keep the following line
- [x] 2.3 Existing Basic, Bearer, and Proxy-Authorization behavior is unchanged
- [x] 2.4 Idempotence covers the new inputs

## 3. Verification

- [x] 3.1 Run the focused tests, `make lint`, `ty check`, and strict OpenSpec validation
