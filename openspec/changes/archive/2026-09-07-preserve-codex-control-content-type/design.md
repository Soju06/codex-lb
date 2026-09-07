## Context

`_build_upstream_headers` preserves native header spelling and order but sets its
media type to JSON. `codex_control_request` subsequently adds a PascalCase header
instead of replacing the existing field. The native Rust transport appends both
case variants into its case-insensitive header map.

## Decisions

Reuse `_replace_header_preserving_position` when restoring the inbound media
type. This retains its first spelling and position, removes duplicates, and
supports opaque non-JSON bodies such as realtime SDP. For a request without body
or inbound media type, remove all case variants of the synthesized field.

The change does not alter the shared Responses header builder: Responses still
requires JSON. Authentication, account ownership, failover, and privacy policies
continue through the existing control service.

## Validation

Public search tests use a Codex Desktop user agent and real control-header
construction, stubbing only the final upstream HTTP call. Header-count and raw
body assertions expose the failure that earlier service-level mocks missed.
Client tests also cover casing, SDK callers, SDP, and bodyless requests.
