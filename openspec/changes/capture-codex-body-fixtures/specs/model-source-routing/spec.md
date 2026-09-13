## ADDED Requirements

### Requirement: The provider-portability fixture corpus records provenance and the gate asserts the recorded verdict

The provider-portability fixture corpus MUST carry a machine-readable
provenance entry per fixture recording its origin (`synthetic` or `captured`),
model slug, transport, capture time, Codex client version, model-catalog digest,
sanitisation list, whether it carries client telemetry, and the expected
outcome: the expected portability view (admitted, or declined with its reason
and detail) and the expected portability verdict for a named set of declared
tool types. The provenance entry MUST live beside the body and MUST NOT be
embedded in it, because the portability view declines unknown top-level fields.
The gate MUST project every fixture through the request path's own validation
dispatch — the OpenAI-compatibility decision the proxy makes for the request,
not a fixed request model — because a real Responses-Lite body carries no
instructions field and fails direct validation. The gate MUST assert the
recorded view and the recorded verdict rather than a fixed expectation, so a
captured body that cannot be served by a model source is a passing fixture that
states that fact. The gate MUST pin the sanitiser's field sets against the
proxy's telemetry-stripping constants and the portability view allowlist, MUST
keep the files on disk, the provenance entries and the rendered corpus table in
agreement in both directions, MUST require every captured fixture to record a
client version and a catalog digest, and MUST include the provenance in every
failure message.

#### Scenario: A captured fixture records a decline and the gate passes

- **GIVEN** a captured Codex body whose provenance records a non-portable
  verdict with its reason and detail
- **WHEN** the fixture gate runs
- **THEN** the projected verdict equals the recorded one and the gate passes
- **AND** the failure message format would name the origin, slug, client
  version and transport

#### Scenario: A wrong recorded verdict fails the gate

- **GIVEN** a fixture whose provenance records a decline reason the projection
  does not produce
- **WHEN** the fixture gate runs
- **THEN** the gate fails and reports both the recorded and the projected
  reason

#### Scenario: A body and its provenance drift apart

- **WHEN** a fixture body exists without a provenance entry, a provenance entry
  names a body that does not exist, or the rendered corpus table disagrees with
  the provenance about a fixture's origin
- **THEN** the gate fails

#### Scenario: A pre-strip fixture declares the telemetry it carries

- **GIVEN** a fixture that exists to exercise the proxy's telemetry stripping
- **WHEN** its provenance declares that it carries client telemetry
- **THEN** the gate accepts the telemetry fields in that body and only in that
  body
- **AND** the privacy gate still rejects any live identifier, operator path or
  credential shape in it

#### Scenario: A sanitised fixture may not carry telemetry

- **GIVEN** a fixture whose provenance declares that it carries no client
  telemetry
- **WHEN** the body nevertheless contains a telemetry field
- **THEN** the gate fails
