## ADDED Requirements

### Requirement: Supported harnesses provide observational conversation metadata

The proxy request-log metadata helper MUST detect a conversation ID only for
the first matching user-agent rule in this ordered table:

- `opencode` uses `x-parent-session-id`, then `x-opencode-session`, then
  `x-session-id`, then `x-session-affinity`.
- `codex` uses `thread-id`.

User-agent prefix matching MUST ignore surrounding whitespace and case. Header
name matching MUST be case-insensitive. The helper MUST use the first configured
header whose value is non-empty after trimming surrounding whitespace, and MUST
preserve the remaining conversation ID exactly. Detection MUST NOT reject,
rewrite, route, or otherwise alter the proxied request.
If `x-parent-session-id` is blank, detection MUST fall through to the next
configured header rather than producing a null conversation ID.

#### Scenario: Codex uses thread-id

- **GIVEN** a request has user-agent `codex/1.2` and `thread-id: " conv-a "`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `conv-a`

#### Scenario: OpenCode uses ordered fallback headers

- **GIVEN** a request has user-agent `opencode/1.0`, an empty
  `x-parent-session-id`, an empty `x-opencode-session`, `x-session-id: fallback`,
  and `x-session-affinity: affinity`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `fallback`

#### Scenario: OpenCode parent session takes precedence

- **GIVEN** a request has user-agent `opencode/1.0`,
  `x-parent-session-id: parent`, `x-opencode-session: child`,
  `x-session-id: fallback`, and `x-session-affinity: affinity`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `parent`

#### Scenario: Prefix and header matching ignore case

- **GIVEN** a request has user-agent ` CODEX/1.2 ` and header `Thread-Id:
  conv-b`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `conv-b`

#### Scenario: Unsupported harnesses produce null metadata

- **GIVEN** a request has no user-agent or has an unsupported user-agent and
  includes a configured conversation header
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is null
- **AND** the request continues through the proxy unchanged

### Requirement: Conversation metadata is nullable and indexed in request logs

The request-log persistence model MUST store `conversation_id` as a nullable
string and MUST provide an index named `idx_logs_conversation_id`. Existing rows
MUST remain valid with a null conversation ID. Empty or whitespace-only detected
values MUST be persisted as null.

#### Scenario: Known conversation ID is persisted

- **GIVEN** request-log metadata contains a non-empty conversation ID
- **WHEN** the request log is persisted
- **THEN** the stored `conversation_id` equals the trimmed ID

#### Scenario: Missing conversation ID remains nullable

- **GIVEN** request-log metadata contains no usable conversation ID
- **WHEN** the request log is persisted
- **THEN** the stored `conversation_id` is null

### Requirement: Conversation metadata propagates through every request-log path

The proxy MUST carry the detected nullable `conversation_id` into the shared
request-log persistence sink for HTTP and WebSocket requests, including normal
requests and preflight errors, and the compact, control, transcription, file,
warmup, thread-goal, and model-source paths. WebSocket finalization and HTTP
logging MUST preserve the same value derived from the inbound request headers.

#### Scenario: Normal HTTP logs retain the inbound conversation

- **GIVEN** a supported Codex or OpenCode request reaches the normal HTTP
  request-log path with a usable conversation header
- **WHEN** the path writes or finalizes its request log
- **THEN** the persisted log contains that conversation ID

#### Scenario: WebSocket logs retain the inbound conversation

- **GIVEN** a supported request reaches the WebSocket request-log path with a
  usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Preflight errors retain the inbound conversation

- **GIVEN** a supported request reaches the HTTP preflight-error log path with
  a usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Compact logs retain the inbound conversation

- **GIVEN** a supported request reaches the compact log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Control logs retain the inbound conversation

- **GIVEN** a supported request reaches the control log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Transcription logs retain the inbound conversation

- **GIVEN** a supported request reaches the transcription log path with a
  usable conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: File logs retain the inbound conversation

- **GIVEN** a supported request reaches the file log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Warmup logs retain the inbound conversation

- **GIVEN** a supported request reaches the warmup log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Thread-goal logs retain the inbound conversation

- **GIVEN** a supported request reaches the thread-goal log path with a usable
  conversation header
- **WHEN** that path persists its request log
- **THEN** the persisted log contains the detected conversation ID

#### Scenario: Model-source logs retain the inbound conversation

- **GIVEN** a model-source request has a supported conversation header
- **WHEN** the model-source path persists its request log
- **THEN** the persisted log contains the detected conversation ID
