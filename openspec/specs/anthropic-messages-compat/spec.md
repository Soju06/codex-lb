# anthropic-messages-compat Specification

## Purpose

Define Anthropic Messages API compatibility contracts so Claude Code and direct Anthropic clients can use both client-defined and Anthropic-defined tools through agent-lb without protocol rewriting.

## Requirements

### Requirement: Anthropic Messages accepts custom and defined tool declarations

The service MUST accept client-defined Anthropic tools that provide `name` and `input_schema`. The service MUST also accept Anthropic-defined tools that provide a versioned `type` and `name` without requiring `input_schema`. Accepted tool declarations and their optional fields MUST be forwarded upstream without changing the caller-selected tool type.

#### Scenario: Existing custom tool remains valid

- **WHEN** a client sends `/v1/messages` with a tool containing `name`, `description`, and `input_schema`
- **THEN** the service accepts the request and forwards the custom tool fields upstream

#### Scenario: Basic web search server tool is accepted

- **WHEN** a client sends `/v1/messages` with `{"type":"web_search_20250305","name":"web_search"}`
- **THEN** the service accepts the request without requiring `input_schema`
- **AND** forwards `type` and `name` unchanged

#### Scenario: Dynamic-filtering web search server tool is accepted

- **WHEN** a client sends `/v1/messages` with `{"type":"web_search_20260209","name":"web_search"}` and supported optional fields
- **THEN** the service accepts the request without requiring `input_schema`
- **AND** forwards the selected type and optional fields unchanged

### Requirement: Anthropic server-tool responses remain protocol transparent

The service MUST preserve Anthropic server-tool streaming and non-streaming response bodies on the downstream `/v1/messages` connection. Internal usage extraction MUST NOT remove or rewrite server-tool use or result blocks.

#### Scenario: Web search stream passes through

- **WHEN** Anthropic returns server-tool use and web-search result events for an accepted request
- **THEN** the downstream client receives those events unchanged and in upstream order

### Requirement: Authentication recovery is bounded and does not misclassify permission failures
On an Anthropic Messages HTTP 401, the proxy SHALL attempt credential recovery once per account before failing over. If another request already replaced the rejected access token, it SHALL reuse the newer token without forcing another refresh. HTTP 403 and repeated access-token rejection SHALL NOT alone permanently disable OAuth credentials. Permanent refresh failures remain authoritative.

### Requirement: Exhausted pools return errors by default
Streaming requests to an exhausted Anthropic pool SHALL return a structured error with available reset timing by default. Holding a stream open for quota recovery SHALL require explicit operator opt-in through `anthropic_pool_exhausted_wait_enabled`.
