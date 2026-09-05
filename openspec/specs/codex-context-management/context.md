# Rationale and limits

The source contract is OpenAI Codex rust-v0.153.1, `codex-rs/ext/history-notes/src/{backend,extension,tools}.rs`. It identifies a root session UUID and an agent path. The backend stores notes and history; codex-lb stores only ownership and participation IDs.

For example, inference starts on A and records A as owner and participant. When an eligible complete request rotates to B after a pre-visible quota rejection, B becomes another participant. Notes stay on A. A history search calls both A and B with the same original query. This also covers child agents using the same root session and their own agent paths.

The native client accepts one `encrypted_output` string per tool result. The proxy therefore returns an authenticated encrypted container and unfolds it on the following Responses request into multiple native `encrypted_content` parts. The native ciphertext stays opaque. A live Astra probe recovered synthetic markers from two such parts. Only ciphertext authenticated through these containers is eligible for the context-tool replay projection. Arbitrary encrypted reasoning remains account bound.

Combining opaque partitions cannot provide exact global JSON sorting, deduplication or pagination. The model receives instructions to combine the results and apply the original query limits. This is useful cross-account recovery, with a different aggregation contract from a single native backend result.

The database keeps ownership tombstones after account/key deletion so a session cannot silently acquire a new namespace. Ordinary startup migration creates the two tables; no historical participation can be inferred for sessions predating this implementation. Start a new session for complete history tracking. Downgrading removes these bindings, so do not resume old context sessions after a downgrade/re-upgrade.

Keep the database and existing `encryption.key` together across restarts. Replicas need the same database and encryption key to route context and unwrap stored tool results. Transparent replay through cross-replica forwarding or after restoring durable request state is not established: in-memory ciphertext verification evidence is deliberately not accepted from client JSON. Such paths may retain strict account ownership.

`docs/codex-context-management.md` describes setup and failure behavior. Integration tests use disposable databases and simulated upstream responses. Live CLI checks used an isolated two-account pool, synthetic notes, a quota rejection injected before provider dispatch, and a proxy restart followed by `new_context` and history recovery.

HTTP streaming binds session/API-key ownership before starting the upstream iterator, then records participation after parsing the first event with a classified type. SSE comments and unclassified frames are forwarded without recording participation; a later valid event still records the account. A startup failure leaves the ownership fence intact without adding an unused history account. WebSocket persistence finishes before the sent timestamp is set; no database await separates that timestamp from send.

The migration refuses pre-existing context tables before creating either one, so downgrade can only remove tables created by its successful upgrade. Historical test fixtures omit these new tables when simulating an older schema.
