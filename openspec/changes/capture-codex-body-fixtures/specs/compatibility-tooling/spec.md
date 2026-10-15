## MODIFIED Requirements

### Requirement: Traffic captures fail safe for credentials and body content

The capture addon MUST replace authorization, API-key, cookie, and proxy
credential header values in every capture mode. Metadata-only body capture MUST
be the default and MUST replace sensitive prompt, generated text, tool argument,
tool output, and encrypted-content strings with deterministic digest-and-length
metadata while retaining protocol structure. Raw body capture MUST require an
explicit full mode, and generated capture and report artifacts MUST be excluded
from version control, with exactly one exception: a sanitised request body MAY
be committed under `tests/fixtures/codex_bodies/` when it carries a recorded
provenance entry and the fixture privacy scan passes over the whole corpus.
Raw captures, header sidecars and run manifests MUST NOT be committed even when
their credential values were disposable.

#### Scenario: Default capture observes an authenticated request

- **WHEN** metadata mode captures a request with bearer credentials and prompt
  text
- **THEN** the credential value is absent from the record
- **AND** the raw prompt is absent
- **AND** stable digest-and-length metadata remains available for same-run
  equality comparison

#### Scenario: Operator explicitly requests full bodies

- **WHEN** the addon is configured for full body capture
- **THEN** request and response bodies are retained for deep investigation
- **AND** credential headers remain redacted

#### Scenario: A sanitised fixture body is committed

- **GIVEN** a captured request body that has been sanitised
- **AND** a provenance entry recording its origin, model slug, transport,
  client version, catalog digest and sanitisation list
- **WHEN** the fixture privacy scan runs over the corpus in strict mode, with no
  arguments beyond the corpus root, because the bodies permitted to keep bare
  client telemetry key names are read from their own provenance entries
- **THEN** the scan passes and the body may be committed
- **AND** the raw capture, its header sidecar and its run manifest remain
  excluded from version control

#### Scenario: A hand-edited fixture body is reported by the residual scan

- **GIVEN** a candidate fixture body under `tests/fixtures/codex_bodies/`
- **WHEN** it contains a credential shape, a non-placeholder UUID, an absolute
  filesystem path outside the placeholder namespace and the paths the Codex
  client itself emits, an email address, an account or host name in a shape that
  identifies it as one, an environment-context tag or skill inventory holding a
  value the corpus does not declare, or a Codex telemetry key the fixture has
  not declared it carries
- **THEN** the residual scan fails and names the finding kinds
- **AND** the report does not echo the offending value
- **AND** the scan is a defence in depth over a body the structural allowlist
  already rebuilt, not the condition under which a capture may be committed:
  it matches patterns over bytes and therefore cannot be complete

#### Scenario: A capture is scanned before the rebuild as well as after it

- **GIVEN** a captured body and the fixture the allowlist rebuilds from it
- **WHEN** the residual scan runs
- **THEN** it reports the kinds found in the captured body and the kinds found
  in the rebuilt body separately
- **AND** a kind present in the captured body and absent from the rebuilt one is
  reported as evidence about the rebuild and never as a pass
- **AND** the rebuild refuses to write a fixture while any kind remains in the
  rebuilt body

## ADDED Requirements

### Requirement: Codex request bodies are captured without credentials or upstream contact

The repository MUST provide a single command that captures a real Codex
Responses request body without ChatGPT credentials, without contacting any
upstream service, and without consuming subscription quota. The command MUST
serve the Codex client from a loopback origin that answers model discovery from
an operator-supplied catalog file and answers Responses with a deterministic
lifecycle, and MUST persist the decoded request bytes together with a header
sidecar and a run manifest recording the client version, the requested and the
observed transport, the catalog digest and a SHA-256 attestation per artifact.
The origin MUST serve every transport the client may choose from the provider
configuration the run generates, and the recorded transport MUST be the one the
body arrived on rather than the one the run asked for. Where a transport primes
the request context before sending the turn, the captured body MUST be the frame
that carries the transcript. Isolation MUST be
enforced by an unprivileged network namespace whose only interface is loopback,
so that external egress is impossible rather than unconfigured; disabling the
namespace MUST require a second, explicit acknowledgement flag. The command
MUST determine whether that isolation holds by observing the interfaces its own
network namespace has, MUST refuse to capture when any interface beyond loopback
is present, and MUST record the observed interfaces in the manifest; no
environment variable may suppress the isolation, and the recorded attestation
MUST NOT be derived from the flags the run was given. The command
MUST refuse, before starting any subprocess or server, an output directory
inside the repository, under a temporary filesystem, or reached through a
symlink; an exported Codex home directory holding stored credentials, which MUST
be refused rather than silently overwritten by the throwaway home the run
creates; an environment carrying proxy or upstream configuration, including any
outbound proxy variable in either spelling, because the Codex client routes even
a loopback request through it; a non-loopback origin address; and a
repository configuration or environment file supplied as the model catalog. The
model catalog MUST be pinned to a file and its digest recorded, because the
Codex model manager refetches discovery whenever the client version differs
from its cache; the client version MUST be recorded as the primary provenance
key, because the client layers bundled model overrides on top of the served
catalog and the catalog therefore does not determine the body.

#### Scenario: A capture run produces a body with no upstream contact

- **GIVEN** a pinned model catalog file and an installed Codex client
- **WHEN** the capture command runs for a model slug
- **THEN** the request body is persisted verbatim after content-encoding
  decoding
- **AND** the run manifest records the client version, transport, catalog
  digest and per-artifact digests
- **AND** no request leaves the loopback interface

#### Scenario: A websocket run captures the turn and not the context prewarm

- **GIVEN** a provider configuration that offers websockets and a client that
  chooses them
- **WHEN** the client primes the request context and then sends the turn
- **THEN** the captured body is the frame carrying the transcript
- **AND** the priming frame is retained under its own artifact name, because on
  the Responses-Lite lane it is where the additional-tools bundle travels
- **AND** the manifest records the transport the body arrived on, so a run that
  fell back to HTTP is not reported as a websocket capture

#### Scenario: The capture refuses an exported credentialed home

- **GIVEN** an exported Codex home variable naming a directory that holds
  stored credentials
- **WHEN** the capture command runs
- **THEN** the command refuses before creating the capture directory, the
  throwaway home, the origin or the client
- **AND** no capture artifact is written

#### Scenario: The capture refuses an unsafe output location or catalog

- **WHEN** the output directory resolves inside the repository, under a
  temporary filesystem, or through a symlink, or the supplied catalog is a
  repository configuration or environment file
- **THEN** the command refuses before starting any subprocess
- **AND** the refusal names the rejected path and the accepted alternatives

#### Scenario: The capture refuses a polluted environment

- **WHEN** the invoking environment carries a proxy or upstream configuration
  variable
- **THEN** the command refuses and names every offending variable
- **AND** the child process it would have started never inherits one

#### Scenario: The capture refuses to attest isolation it cannot observe

- **GIVEN** an invoking environment carrying any variable that a previous
  version treated as "already inside the network namespace"
- **WHEN** the capture command runs without an explicit egress acknowledgement
- **THEN** the command still re-executes itself into a network namespace
- **AND** a run that finds an interface beyond loopback refuses before creating
  the capture directory, the origin or the client
- **AND** no manifest claims isolation that was not observed

### Requirement: A committed fixture is rebuilt from a structural allowlist

A captured body MUST be committed only as a body *reconstructed* from an
explicit structural allowlist, never as the captured body with recognised
substrings rewritten. Every node MUST be matched against a declared rule, and a
rule MUST either preserve a value drawn from a closed domain the portability
decision, the replay predicate or the corpus gate reads — an object key, a type,
role, status, phase, effort, verbosity or other discriminator, a JSON Schema
keyword, a boolean, a bounded number, a model slug in slug shape, or a
client-vocabulary tool name — or replace the value wholesale with a substitute
derived only from its JSON path. A preserved number MUST be bounded in magnitude
and in decimal precision, and a number inside a tool's parameter schema MUST be
replaced rather than preserved, because an arbitrary-precision numeric slot is
an arbitrary-bandwidth channel that a walk over strings cannot see. The model
slug is preserved by shape rather than from a closed set, so the corpus gate
MUST assert that a committed body names the slug its provenance entry records. Free-form fields, including instructions, message content text,
tool-call arguments, tool-result output, tool descriptions, JSON Schema property
names and enumeration values, metadata keys, filesystem paths, URLs and every
identifier, MUST be replaced wholesale. A replaced string MAY leave behind only:
whether it was blank, because the replay predicate reads that; for a URL, the
scheme, because the scheme decides account-neutrality; for an identifier, which
other identifiers it equalled; and for a key in an open namespace, its rank
among its siblings, because the substitutes must be stable across rebuilds. A node no rule describes MUST stop the rebuild and name its path, and MUST
NOT be dropped silently, because a missing key changes the key set the replay
predicate validates.

The rebuild MUST remove at least the client telemetry fields the proxy strips
from a source-routed body, MUST remove the websocket frame envelope from a
websocket capture — which is persisted verbatim because on that transport the
frame is the request body — and MUST remove the same stream-option keys the
proxy removes, dropping that object only when the removal empties it. Every
identifier MUST be replaced by a placeholder from a recognisable family, and
identifiers that refer to one another, such as a tool call and the output that
settles it, MUST still refer to one another afterwards. It MUST NOT add a field
the captured body did not carry, because the portability view declines unknown
top-level fields and a fabricated key would change the recorded verdict. It MUST
be idempotent and MUST report what it changed without echoing any removed value.
Neither the rebuild nor the residual scan may take any part of its vocabulary
from the machine it runs on, so a capture rebuilds to the same fixture and a
corpus reaches the same verdict on every host.

The rebuild MUST verify its own output: no string the captured body carried,
outside the declared allowlist, may appear in the reconstructed body, and the
tool MUST refuse to write a fixture when one does.

#### Scenario: An unreviewed node stops the rebuild

- **WHEN** a captured body carries a top-level field, a nested field, an input
  item type, a tool type, a JSON Schema keyword or a discriminator value the
  rules do not describe
- **THEN** the rebuild fails and names the path
- **AND** the refusal does not echo the unreviewed key or value
- **AND** no fixture is written

#### Scenario: The planted leak shapes do not reach the fixture

- **GIVEN** a captured body carrying an operator filesystem path immediately
  after a colon, in an account-at-host transfer target, as a `file://` URL,
  inside a JSON string nested in a tool call's arguments, escaped as `\uXXXX`
  and wrapped in base64, together with an email address and a credential-shaped
  token
- **WHEN** the body is rebuilt
- **THEN** none of those strings appears in the output, including the ones no
  residual pattern recognises
- **AND** the verification walk reports no surviving captured string

#### Scenario: Absence is preserved

- **GIVEN** a captured Responses-Lite body with no instructions, no tool array
  and no stream options
- **WHEN** it is rebuilt
- **THEN** none of those fields is present in the output

#### Scenario: The recorded verdict survives the rebuild

- **GIVEN** a captured body whose tool declarations and input items carry the
  fields that decide its portability verdict
- **WHEN** it is rebuilt
- **THEN** every tool declaration keeps its type and its field set, every input
  item keeps its type, role and identifier presence, and a tool call and its
  output still share an identifier
- **AND** the portability view and verdict are the ones the provenance entry
  records, unchanged by the rebuild
- **AND** re-running the rebuild produces an identical result

### Requirement: Committing a fixture requires an explicit human acknowledgement

Writing a rebuilt body into the committed fixture corpus MUST require an
explicit acknowledgement flag naming the fact that the operator has read the
rebuilt body, and MUST refuse without it. The documentation MUST state that
reading the diff, and not the residual pattern scan, is the boundary that keeps
operator content out of the repository.

#### Scenario: The corpus destination refuses an unacknowledged write

- **WHEN** the rebuild is asked to write into the committed fixture corpus
  without the acknowledgement flag
- **THEN** it refuses and names the flag
- **AND** it writes nothing
- **AND** a destination in a subdirectory of the corpus refuses in the same way
- **AND** a header sidecar or redaction log aimed anywhere under the corpus is
  refused outright, with or without the flag
- **AND** the same command writing outside the corpus needs no flag
