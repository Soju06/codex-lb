## ADDED Requirements

### Requirement: Sequential multi-file account import

The Accounts import dialog SHALL accept one or more auth.json files and send them sequentially in selection order through the existing single-file import callback. It SHALL disable selection and submission and prevent dialog dismissal while imports run. The dialog SHALL close and clear selection only after every selected file succeeds. A failure SHALL keep the dialog open, retain the failed and unattempted files for retry, and exclude already successful files from retries. The existing API multipart contract and successful-import refresh behavior SHALL remain unchanged.

#### Scenario: Successful batch

- **WHEN** an operator selects multiple files and submits
- **THEN** each file is imported once in selection order
- **AND** the dialog closes and clears selection after all succeed

#### Scenario: Partial failure and retry

- **WHEN** a later file fails after an earlier file succeeds
- **THEN** subsequent files are not attempted
- **AND** the dialog retains only failed and unattempted files for retry

#### Scenario: In-flight controls

- **WHEN** an import is pending
- **THEN** the file input and submit action remain disabled
