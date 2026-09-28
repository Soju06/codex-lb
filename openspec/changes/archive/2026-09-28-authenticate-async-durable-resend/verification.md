# Verification

- The HTTP Responses owner-loss regression failed before the guard: account B returned 200 rather than the required 502. The same test passed after the guard; a second route case with a genuine async call and delayed output also refuses the forged synchronous ID. The direct durable verifier regression passes.
- Six scoped integration/unit files passed: 625 cases. Focused ASGI route and WebSocket transport QA passed 5 cases using a fresh SQLite database and removed its temporary resources.
- Changed Python files have clean LSP diagnostics, Ruff lint and format, and ty checks. The isolated wheel build passed and its temporary wheel was removed. `git diff --check` passed.
- The MODIFIED delta and owning main requirement have identical text and all 12 retained/new scenarios. `openspec validate authenticate-async-durable-resend --strict` passed. Pinned `@fission-ai/openspec@1.11.0` validated all 66 main specs strictly; locally installed 1.4.1 rejects pre-existing MUST/SHALL omissions outside this change.
- Exact invocations, logs, cleanup receipts, and known limitations: `/tmp/ulw-20260928-pr2099-evidence.md`. Full local CI was previously green at public head and intentionally not repeated for this focused repair. No push, comment, merge to main or deployment occurred.
