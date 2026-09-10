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

Select the external egress port from the same source as the database URL. Parse
`externalDatabase.url` only when chart-managed Secret rendering makes it the
active source; existing Secret and ExternalSecret inputs use the separately
configured `externalDatabase.port` because Helm cannot inspect their contents.
For an active direct URL, honor an explicit authority port, SQLAlchemy-compatible
percent-encoded ASCII query-level `port` overrides, or ports embedded in
query-level `host` values. Collect every unique effective port for multihost
failover, ignore blank query items, distinguish portless IPv6 literals, and
normalize ports to decimal integers in the Kubernetes range. A portless query
host inherits the authority port before falling back to PostgreSQL's 5432
default. Otherwise use
`.Values.externalDatabase.port | default 5432`, matching the synthesized or
secret-backed URL contract. Keep the bundled branch's service-selected TCP 5432
rule unchanged because it targets the chart-managed PostgreSQL service.

The regressions render discrete custom/default fields, direct URL authority,
encoded query, IPv6, and multihost forms, inactive stale URLs beside
secret-backed sources, invalid ports, and bundled mode. They compare the
generated Secret and NetworkPolicy's machine-consumed port values without
requiring a cluster.

## Risks / Trade-offs

- A template typo could affect both application and migration connectivity.
  Mitigation: parse real Helm output and retain the default-port control.
- Broadening external egress to the configured port remains host-unrestricted,
  matching the existing policy design. Host/CIDR restriction remains out of
  scope and available through operator-managed policy controls.
