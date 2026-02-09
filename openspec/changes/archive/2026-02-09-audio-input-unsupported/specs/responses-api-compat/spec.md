## ADDED Requirements

### Requirement: Allow input_file file_id pass-through
The service MUST accept `input_file.file_id` in Responses input items and MUST forward it to upstream without local rejection.

#### Scenario: input_file file_id passes through
- **WHEN** a request includes an input item with `{"type":"input_file","file_id":"file_123"}`
- **THEN** the service forwards the request to upstream without local validation error
