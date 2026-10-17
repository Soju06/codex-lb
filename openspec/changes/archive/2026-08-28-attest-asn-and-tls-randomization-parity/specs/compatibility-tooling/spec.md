## ADDED Requirements

### Requirement: Controlled captures can attest ASN from an offline database

The traffic capture addon MUST accept an optional operator-supplied ASN MMDB
path and MUST resolve the capture socket's peer address locally without making
a network lookup. When configured, it MUST record the ASN number, a digest of
the ASN organization, and database digest/build provenance, and MUST NOT retain
the raw source address or organization name. The analyzer MUST compare ASN
evidence only when A and C attest the same observer id and role and the same
database digest. Missing, failed, or incompatible evidence MUST be reported as
unobserved rather than pass. Only evidence captured with observer role `origin`
MAY be described as public egress-ASN evidence.

#### Scenario: Same controlled origin and database observe the same ASN

- **GIVEN** Path A and Path C use the same origin observer and ASN database
- **AND** both local lookups return the same ASN number and organization digest
- **WHEN** the server-observable report is generated
- **THEN** ASN parity passes with controlled-origin claim scope
- **AND** the record contains neither the raw source address nor organization
  name

#### Scenario: Database provenance differs

- **GIVEN** Path A and Path C contain ASN observations from different database
  digests
- **WHEN** the report compares ASN evidence
- **THEN** ASN parity is unobserved due to incompatible database provenance
- **AND** equal ASN numbers are not promoted to a pass

### Requirement: TLS extension-order parity is calibrated against direct traffic

The analyzer MUST accept an optional second direct-Codex capture as a TLS
randomization reference. For HTTP JSON, HTTP SSE, and WebSocket independently,
it MUST deduplicate records from the same ClientHello, require a configurable
minimum sample count in every compared cohort, and compare invariant TLS
capability fields exactly. When invariant profiles match, it MUST summarize
pairwise extension precedence and order entropy and MUST compare the A/C order
distance against a deterministic 95% bootstrap limit derived only from the two
direct cohorts. Raw JA3 and ClientHello hashes MUST remain informational and
MUST NOT be used as the extension-order parity gate. Missing samples MUST be
reported as unobserved rather than pass.

#### Scenario: Randomized orders remain within direct variance

- **GIVEN** two sufficiently sampled direct cohorts have the same stable TLS
  profile and randomized extension orders
- **AND** the codex-lb cohort has the same stable profile and an order distance
  within the direct-derived 95% limit
- **WHEN** TLS randomization parity is analyzed
- **THEN** the transport cohort passes
- **AND** differing raw JA3 hashes remain visible as informational evidence

#### Scenario: Load balancer emits a fixed or shifted order profile

- **GIVEN** the direct cohorts demonstrate randomized extension ordering
- **AND** the codex-lb cohort emits an order distribution beyond the
  direct-derived 95% limit
- **WHEN** TLS randomization parity is analyzed
- **THEN** that transport cohort fails
- **AND** stable-profile equality does not conceal the distribution mismatch

#### Scenario: A cohort has too few independent handshakes

- **GIVEN** any direct or codex-lb cohort has fewer than the configured minimum
  deduplicated ClientHello samples
- **WHEN** TLS randomization parity is analyzed
- **THEN** that transport cohort is reported as unobserved
- **AND** it is not reported as a pass
