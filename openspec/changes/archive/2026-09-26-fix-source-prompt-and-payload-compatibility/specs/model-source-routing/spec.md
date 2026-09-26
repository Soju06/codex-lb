## ADDED Requirements

### Requirement: Prompt template references retain source ownership

For direct-source Responses requests, the proxy MUST resolve `prompt.id` in the presenting API-key and exact public-model scope. Successful source requests MUST durably publish their prompt reference before delivering completion, including streaming completion, so another backend can resolve it. A prompt unknown to a pool or conflicting with another reference MUST be rejected before dispatch. Credential replacement MUST invalidate recorded prompt continuity, including after active ownership expiry. Original external prompts MUST retain single-source compatibility when no conflict exists. Unknown prompt IDs introduced by source overrides MUST be rejected even for one source.

#### Scenario: Prompt conflicts with an anchor

- **WHEN** a request combines source A's response anchor with a prompt recorded for B or an unknown prompt
- **THEN** neither source MUST receive the request

#### Scenario: Prompt learned before expanding a pool

- **WHEN** a successful single-source request used an external prompt and a second source is later assigned
- **THEN** another backend MUST continue that prompt on its recorded owner
- **AND** replacing that owner's credential MUST reject the old prompt before dispatch

#### Scenario: Prompt supplied by source overrides

- **WHEN** a source override supplies a prompt ID
- **THEN** that candidate MUST be eligible only when that ID resolves to its current credential
- **AND** an invalid candidate MUST NOT block another valid candidate

### Requirement: Prompt variable files cannot enter direct sources

An effective source request with a file reference in an `input_file` or `input_image` value of `prompt.variables` MUST be rejected before source admission, quota reservation, or upstream dispatch. This MUST apply to client-supplied and override-supplied variables with one or multiple sources, regardless of a matching prompt or response owner. The proxy MUST NOT redirect override-introduced files to a subscription account. Reference-free text, inline data, and ordinary URL variables MUST retain their existing forwarding behavior. Existing original input-file subscription routing MUST remain unchanged.

#### Scenario: Prompt variable refers to an account file

- **WHEN** a request with a matching source anchor and prompt includes a file ID in a prompt variable
- **THEN** the source MUST NOT receive it and no source quota reservation MUST be created

#### Scenario: Override introduces file-bearing variables

- **WHEN** one candidate's override inserts a file-bearing prompt variable
- **THEN** that candidate MUST be rejected without blocking a safe candidate

#### Scenario: Variables contain reference-free content

- **WHEN** an otherwise eligible request supplies ordinary text, inline file data, or an ordinary image URL as prompt variables
- **THEN** those values MUST reach the selected source unchanged

### Requirement: Original source state is distinct from override state

Reference validation MUST treat client state retained in the direct-source forwarding body as original request state even when subscription-specific cleanup would remove it. A single source with no conflicting ownership MUST accept an original external compaction item following a recognized local compact fallback marker. The proxy MUST still reject unknown override-introduced state and conflicting original state, including on source lookup misses and model normalization.

#### Scenario: Existing compacted history with one source

- **WHEN** an original request contains the local compact fallback marker followed by an external encrypted compaction item and has one eligible source
- **THEN** the request MUST retain single-source external-state compatibility without being rejected as an unknown override

#### Scenario: Compacted history belongs to an unavailable owner

- **WHEN** an original retained compaction reference has a known unavailable or conflicting owner
- **THEN** subscription-specific cleanup MUST NOT erase the evidence and authorize another source or account

### Requirement: Log-probability controls remain source-neutral

A direct-source Responses request with integer `top_logprobs` from 0 through 20 MUST remain eligible for initial source pooling and portable failover when its other state is portable. The value and log-probability include fields MUST be forwarded unchanged on both HTTP routes and streaming requests. Booleans, out-of-range numbers, and other malformed values MUST NOT be classified as neutral by this allowance. Subscription-overflow replay policy MUST remain unchanged.

#### Scenario: A second source is added

- **WHEN** a fresh request with valid `top_logprobs` succeeds with one source and another equivalent source is assigned
- **THEN** the same request MUST remain accepted, including from a streaming SDK client

#### Scenario: Invalid log-probability control

- **WHEN** a pooled request supplies a boolean, out-of-range number, or malformed `top_logprobs` value
- **THEN** this direct-source allowance MUST NOT authorize its dispatch
