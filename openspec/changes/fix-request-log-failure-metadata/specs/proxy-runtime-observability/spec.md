## ADDED Requirements

### Requirement: Request-log metadata keeps local routing failures unbound from upstream status

When request routing fails before contacting upstream, `upstream_status_code` MUST be
`null` even if the internal failure exception carried an HTTP-like status. The
logged `upstream_error_code` MUST keep the local routing code for triage and
analytics.

#### Scenario: Additional quota or plan-routing failure is classified as local

- **WHEN** a request fails with one of `no_plan_support_for_model`,
  `additional_quota_data_unavailable`, or
  `no_additional_quota_eligible_accounts`
- **THEN** request-log metadata stores `upstream_error_code` with that exact code
- **AND** request-log metadata stores `upstream_status_code = null`

