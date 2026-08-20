## Context

The chart builds a direct external database URL from `externalDatabase.host`,
`user`, `database`, and `port`. Its default-deny NetworkPolicy selects the same
application and migration workloads, but the external PostgreSQL egress branch
currently permits only TCP 5432.

## Goals / Non-Goals

**Goals:**

- Keep the generated database URL and external database egress port consistent.
- Prove custom-port and default-port behavior through real Helm rendering.
- Preserve bundled PostgreSQL and existing selector behavior.

**Non-Goals:**

- Restrict external egress by host or CIDR.
- Change `networkPolicy.extraEgress`, workload labels, or chart dependencies.
- Exercise a live Kubernetes cluster or database.

## Decisions

Select the external egress port from the same source as the database URL. When
`externalDatabase.url` is set, parse its host and use an explicit terminal port,
or PostgreSQL's 5432 default when the URL omits one. Otherwise use
`.Values.externalDatabase.port | default 5432`, matching the synthesized URL.
This preserves direct URL precedence without introducing another setting or a
one-off helper. Keep the bundled branch's service-selected TCP 5432 rule
unchanged because it targets the chart-managed PostgreSQL service.

The regressions render discrete custom/default fields, direct URLs with and
without explicit ports, and bundled mode, then compare the generated Secret and
NetworkPolicy's machine-consumed port values. This covers the operator-visible
template surface without requiring a cluster.

## Risks / Trade-offs

- A template typo could affect both application and migration connectivity.
  Mitigation: parse real Helm output and retain the default-port control.
- Broadening external egress to the configured port remains host-unrestricted,
  matching the existing policy design. Host/CIDR restriction remains out of
  scope and available through operator-managed policy controls.
