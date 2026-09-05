# Rationale and limits

The source contract is OpenAI Codex rust-v0.153.1, `codex-rs/ext/history-notes/src/{backend,extension,tools}.rs`. It uses four history routes, five notes tools and `notes/v2/thread_hint` during context assembly. The backend owns the data. A local notes store is not a compatible substitute.

The current proxy control path already handles credential replacement, account scope, refresh, upstream egress routes and bounded transport. The initial implementation requires a key scoped to one account so these account-local operations cannot silently cross accounts. Operators must use the same key for Responses and must not reassign it during a continuing session. General multi-account continuity needs a separate ownership contract, especially because Codex history context identifies the process session and supports cross-agent reads.

A client-side flag can expose `new_context` before any notes backend works. Verification must cover actual endpoint requests, not only tool names. No production credentials or database are part of this change.

The operator guide is `docs/codex-context-management.md`. Its example uses a separate Codex profile and a separate proxy instance. The integration suite uses a disposable SQLite database and fake upstream credentials. Live OpenAI entitlement and context-transition persistence remain deployment smoke checks, not claims made by this patch.
