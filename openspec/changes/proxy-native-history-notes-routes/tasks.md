## 1. Implementation

- [x] 1.1 Register the ten native Codex history and notes v2 POST routes on the
      authenticated control proxy.
- [x] 1.2 Derive control-route affinity from `context.session_id` without
      modifying the upstream body or inbound native headers.
- [x] 1.3 Preserve API-key scoping and prohibit cross-account failover for
      native history-and-notes calls.

## 2. Validation

- [x] 2.1 Add regression coverage for every supported route, encrypted and
      truncation headers, body identity affinity, and unsupported-route/auth
      negative controls.
- [x] 2.2 Run focused route and control tests plus strict OpenSpec validation.

- [x] 2.3 Verify shared hard ownership for notes, marked Responses, compact,
      child threads, and unavailable-owner fail-closed behavior.
