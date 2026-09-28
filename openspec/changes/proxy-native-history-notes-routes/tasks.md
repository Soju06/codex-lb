## 1. Implementation

- [x] 1.1 Register the ten native Codex history and notes v2 POST routes on the
      authenticated control proxy.
- [x] 1.2 Derive control-route affinity from `context.session_id` in a
      dedicated hard `history_session` namespace, seeded from the soft
      process-session owner, without modifying the upstream body or inbound
      native headers.
- [x] 1.3 Preserve API-key scoping and prohibit cross-account failover for
      native history-and-notes calls, including bodies without an identity.
- [x] 1.4 Keep `_ContinuitySource` as the single owner of the source literal
      and alias it from `affinity._CodexSessionSource`.
- [x] 1.5 Leave Responses and compact affinity untouched by the
      `history_ingest_requested` marker.

## 2. Validation

- [x] 2.1 Add regression coverage for every supported route, encrypted and
      truncation headers, body identity affinity, unsupported-route and auth
      negative controls, and the body-without-identity passthrough.
- [x] 2.2 Verify that the first native call follows the process-session owner,
      that a marked Responses turn still rotates off a paused owner, and that
      native calls fail closed without replacing the stored owner.
- [x] 2.3 Run focused route, control, affinity, and selection tests plus Ruff,
      ty, and strict OpenSpec validation.
