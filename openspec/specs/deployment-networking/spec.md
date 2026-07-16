# deployment-networking Specification

## Purpose

Define network exposure and policy contracts so chart deployments default to explicit, least-privilege connectivity.
## Requirements
### Requirement: NetworkPolicy ingress defaults fail closed

When the Helm chart enables `networkPolicy`, it MUST NOT open the main HTTP ingress port to every namespace by default. Namespace-scoped ingress access MUST be rendered only when an explicit allowlist selector is configured, or when the operator supplies an equivalent extra ingress rule.

#### Scenario: Empty ingress namespace selector does not create an allow-all rule

- **WHEN** `networkPolicy.enabled=true`
- **AND** `networkPolicy.ingressNSMatchLabels` is empty
- **THEN** the rendered NetworkPolicy does not include `namespaceSelector: {}`
- **AND** ingress remains deny-by-default unless the operator adds an explicit allow rule

### Requirement: Stock Docker networking explains network switching

The documented portable standalone Docker deployment MUST attach codex-lb to a user-defined bridge network, and stock Compose deployments MUST declare a user-defined default bridge. The documentation MUST state that Docker's embedded resolver can retain stale external forwarding servers across a host network change. It MUST provide a Linux host-network launch option for operators whose host exposes a stable resolver address, and MUST state that a direct DHCP-provided resolver can still become stale in host-network mode. Stock configuration MUST NOT hard-code a public recursive DNS server.

#### Scenario: Standalone quick start uses a user-defined bridge

- **WHEN** an operator follows the documented standalone Docker quick start
- **THEN** the instructions create the codex-lb bridge idempotently
- **AND** start the container with that bridge selected by `--network`

#### Scenario: Compose uses a user-defined default bridge

- **WHEN** Docker Compose renders either stock Compose deployment
- **THEN** the server is attached to a user-defined default bridge
- **AND** the rendered service does not pin a public DNS server

#### Scenario: Linux network-switching launch uses the host resolver path

- **WHEN** a Linux operator selects the documented launch for switching Wi-Fi or other networks
- **THEN** the container uses `--network host`
- **AND** the command does not publish ports with `-p`
- **AND** the documentation requires a stable host resolver address and identifies `systemd-resolved` as the verified setup
- **AND** the documentation warns that a direct DHCP-provided resolver may still become stale
- **AND** the documentation explains the loss of Docker network-namespace isolation

#### Scenario: Portable bridge limitations are explicit

- **WHEN** an operator reads the portable bridge instructions
- **THEN** the documentation does not claim that `127.0.0.11` guarantees forwarder refresh after switching networks
- **AND** it identifies host networking or a host-resolver bridge listener as the stronger Linux options

### Requirement: Shipped overlays that enable NetworkPolicy with Ingress allow ingress-controller traffic

Any values overlay shipped with the chart that sets `networkPolicy.enabled=true` together with `ingress.enabled=true` MUST configure `networkPolicy.ingressNSMatchLabels` (or an equivalent `networkPolicy.extraIngress` rule) so the rendered NetworkPolicy admits ingress-controller traffic to the HTTP port.

#### Scenario: values-prod.yaml admits the ingress controller on the HTTP port

- **WHEN** the chart is rendered with `values-prod.yaml`
- **THEN** the NetworkPolicy contains an ingress rule for port `2455` from a `namespaceSelector` matching the configured ingress-controller namespace labels
- **AND** no rule on the HTTP port uses an empty `namespaceSelector` (the fail-closed requirement is preserved)

### Requirement: Missing NetworkPolicy ingress allowlist warns at install time

WHEN `networkPolicy.enabled=true` AND `ingress.enabled=true` AND both `networkPolicy.ingressNSMatchLabels` and `networkPolicy.extraIngress` are empty, the rendered install NOTES MUST contain a warning that external traffic through the ingress controller will be denied on the HTTP port while pods stay Ready.

#### Scenario: Warning renders for the denying combination

- **WHEN** the chart is installed with `networkPolicy.enabled=true`, `ingress.enabled=true`, and no ingress allowlist configured
- **THEN** the rendered NOTES contain a WARNING naming `networkPolicy.ingressNSMatchLabels` and the denied HTTP port

#### Scenario: Warning absent when an allowlist is configured

- **WHEN** the chart is installed with `networkPolicy.ingressNSMatchLabels` set (for example via `values-prod.yaml`)
- **THEN** the rendered NOTES do not contain the ingress-denied warning

### Requirement: nginx ingress annotations render as a coherent set

All nginx-specific annotations — the streaming-safety set (`proxy-buffering: off`, `proxy-request-buffering: off`, `proxy-read-timeout`/`proxy-send-timeout: 3600`, `proxy-body-size`, `proxy-http-version: 1.1`) AND the sticky-routing set (`upstream-hash-by`, subset options, and the responses sticky/retry annotations) — MUST render only when `ingress.nginx.enabled=true`, and MUST render together. Shipped overlays that enable ingress on an nginx class (`values-staging.yaml`, `values-prod.yaml`) MUST set `ingress.nginx.enabled=true`.

#### Scenario: No nginx annotations without the nginx flag

- **WHEN** the chart is rendered with `ingress.enabled=true` and `ingress.nginx.enabled=false`
- **THEN** neither the main nor the responses Ingress carries any `nginx.ingress.kubernetes.io/*` annotation

#### Scenario: Staging and prod overlays carry the full annotation set

- **WHEN** the chart is rendered with `values-staging.yaml` or `values-prod.yaml`
- **THEN** both Ingress resources carry the streaming-safety annotations AND the sticky-hash annotations

### Requirement: Responses sticky routing defaults are admission-safe on stock ingress-nginx

The default responses-ingress sticky mechanism MUST NOT rely on `nginx.ingress.kubernetes.io/configuration-snippet` (rejected by stock ingress-nginx >= 1.12 at the default `annotations-risk-level`). The default MUST be a snippet-free `upstream-hash-by` key that prefers `x-codex-session-id` and falls back to the `Authorization` header (`$http_x_codex_session_id$http_authorization`). `configuration-snippet` MUST render only when the operator explicitly sets `ingress.responses.nginx.configurationSnippet`.

#### Scenario: Default render is snippet-free

- **WHEN** the chart is rendered with `ingress.enabled=true` and `ingress.nginx.enabled=true` and default values
- **THEN** the responses Ingress carries `nginx.ingress.kubernetes.io/upstream-hash-by: $http_x_codex_session_id$http_authorization`
- **AND** no `nginx.ingress.kubernetes.io/configuration-snippet` annotation is rendered

#### Scenario: Explicit snippet opt-in renders

- **WHEN** the operator sets a non-empty `ingress.responses.nginx.configurationSnippet`
- **THEN** the `configuration-snippet` annotation renders with the configured content
