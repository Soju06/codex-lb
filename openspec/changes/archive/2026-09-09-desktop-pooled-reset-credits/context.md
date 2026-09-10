# Implementation context

Issue #2288 depends on #2286. Read-only inspection used Desktop 26.903.61454 and bundled CLI 0.153.4. Its HTTP reset controller retains credit ID and redemption request ID after transport failure. Native tools use the separate app-server reset RPC and remain outside this integration. No application files changed and no real reset was consumed.

The contribution starts at fetched upstream `efe0f581a18a36f4d491b92a36ef79b0c432d252` and includes Desktop foundation `d31ac538412b3ccd644db34e41557d1307994cd8`. The reset-specific review base is `99473ed1138e2e3c72ca855528a0d662bfeb60e3`. Until #2286 merges, the follow-up PR includes that dependency and provides a reset-only comparison.

The default action selects earliest known expiry. Explicit selection remains explicit. The operator policy defaults off. A permanent owner binding survives cache loss, short-ledger expiration, deletion and reauthorization. Original-account requests preserve native idempotency keys; cross-account keys are caller-scoped. Finish pre-adapter pending attempts before first enabling pooling.
