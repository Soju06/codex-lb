## 1. Line-bounded secret patterns

- [x] 1.1 Apply keyed/bearer/authorization/JSON/Python-repr patterns per CR/LF
      line and preserve original terminators
- [x] 1.2 Redact unterminated JSON secret values through end of line
- [x] 1.3 Consume a glued `:` tail on Bearer tokens

## 2. Regression coverage

- [x] 2.1 Authorization does not swallow the next traceback line in text and JSON
- [x] 2.2 Unterminated JSON secret at end of line is redacted; following line survives
- [x] 2.3 `Bearer abc.def:GLUEDTAIL, status=502` keeps `, status=502` and drops the tail
- [x] 2.4 Existing same-line comma-less Authorization truncation still holds
- [x] 2.5 Idempotence and terminator-byte identity; WARNING+ gate unchanged

## 3. Verification

- [x] 3.1 Prove the three gaps on `upstream/main`, then the focused tests on this head
- [x] 3.2 Run ruff, ty, and strict scoped OpenSpec validation
