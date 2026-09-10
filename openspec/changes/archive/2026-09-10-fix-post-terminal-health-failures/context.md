# Reproduction and scope

Confirmed product defect on upstream main `0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0`, matching issue #2033. The direct streaming path uses `stream: true`; HTTP session bridging is disabled in the existing test fixture. No new setting or special client setup is required. The existing terminal-settlement immutability requirement and the maintainer's September 8 issue analysis require preserving the delivered terminal outcome.

The route-handler regression injects an ordinary account-health persistence failure after a successful reservation settlement. Keyed owner continuations reproduce an abrupt completion error for a first-event failure and two terminal events for later-event or raised upstream failures. An unkeyed stream with visible text followed by a raised quota error also produces two terminals. All four fail on the pinned base and pass with the fix.

The integration regression imports a synthetic account through `/api/accounts/import`, then posts `{"model":"gpt-5.1","instructions":"hi","input":"hello","stream":true}` to `/v1/responses` through the real ASGI app. Upstream yields `response.created`, a text delta, then raises a quota error. The injected health exception produces two terminal events on main and one original `usage_limit_reached` terminal with the fix. It also verifies the exception is logged.

The attempted unkeyed owner-rewrite matrix is not evidence for this post-terminal bug: its health write occurs before terminal delivery. An unkeyed native later-event failure in the tested setup forwards without attempting a health penalty. Those paths are excluded from the regression matrix and unchanged.

The fix wraps six existing post-terminal error-health calls only. It leaves pre-terminal retry/admission decisions, settlement errors, success writes, and cancellation propagation unchanged. No dependency on PR #1905 or another unmerged change is introduced.
