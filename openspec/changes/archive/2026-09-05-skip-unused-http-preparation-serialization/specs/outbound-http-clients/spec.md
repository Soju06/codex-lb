## ADDED Requirements

### Requirement: Responses HTTP preparation serializes only for active consumers

When the effective transport is unconditionally HTTP and the Python HTTP client does not need a pre-serialized body for an active consumer, Responses preparation MUST NOT serialize a full WebSocket-shaped request for a size decision that cannot affect that transport or serialize a full selected payload that no consumer uses. The transmitted HTTP body, metadata finalization and existing headers MUST remain unchanged. WS-eligible transport selection MUST retain its current exact-byte budget and fallback semantics. Enabled payload tracing, native request-body generation, archive ownership and payload-mutating fallback paths MUST retain their current contents and behavior.

#### Scenario: Python HTTP with inactive preparatory-string consumers
- **WHEN** a large Responses request uses explicit Python HTTP with raw payload tracing inactive
- **THEN** preparation performs no unused full-body serialization for WS size selection or an unused selected payload string
- **AND** the real upstream receives exactly the existing serialized HTTP body

#### Scenario: A consumer requires a serialized payload
- **WHEN** native transport, raw payload tracing or WS-eligible size/send handling requires a full payload string
- **THEN** the required serialization remains available with current contents
- **AND** payload changes before a later consumer are reflected without reusing stale bytes
