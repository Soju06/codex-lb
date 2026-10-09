# Verified contribution results

- Base: upstream main `f8ffbac2099a113fba54dfd8d77774f5bca80ffa` (1.25.0-beta.9).
- Original-validator regression proof: five expected schema failures; the public transparent PNG generation route also reproduces HTTP 400 with the original validator.
- Final image suites: 230 passed. Ordinary Responses/errors/account-refresh/sticky-session suites: 302 passed, three pre-existing skips. Test databases are disposable and upstream calls are stubbed.
- Repository-wide lint and type checks passed. The new OpenSpec change passes strict validation; canonical capabilities pass normal validation. Optional repository-wide strict diagnostics match untouched main exactly.
- Current main host selection is retained and asserted by the new route tests. The runtime diff changes only image validation; settings, routing, auth, budgets, translation, and upstream error handling are preserved.
- Prior live verification of the same validator fix on stable 1.24.0 returned genuine alpha for PNG/WebP generation, PNG editing, and Codex built-in generation. Exact returned dimensions/quality can still vary upstream; this change does not transform image bytes or promise exact sizing.
