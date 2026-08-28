## ADDED Requirements

### Requirement: Compact trimming preserves prioritised historical side effects

The service MUST retain recognised historical side-effect tool calls as bounded
priority context when an oversized compact input is trimmed. It MUST use the
same side-effect classifier as downstream replay deduplication. This includes
code-mode `exec` and `collaboration` wrapper calls as well as their lower-level
tool spellings and recognised parallel batches.

For each retained historical side effect, compact trimming MUST retain its
matching call and output together. The service MUST reserve space for that
complete pair before selecting optional ordinary head or tail context. Required
state anchors and the current required item remain mandatory; if they leave no
room for a historical pair, the service MAY drop that pair together and retain a
trim marker instead.

A recognised side-effect call without a non-empty `call_id` MUST NOT be
retained as a historical side-effect anchor, because it cannot form a verified
call/output pair.

#### Scenario: Code-mode side effect survives an oversized compact input

- **WHEN** an oversized compact input contains a historical custom `exec` or
  `collaboration` call with its matching output outside required state context
- **THEN** the trimmed upstream input retains both the call and its output when
  the pair fits with required state
- **AND** optional ordinary tail context is dropped before that pair

#### Scenario: Historical side-effect pair cannot fit with required state

- **WHEN** required state anchors and the current required item leave no room
  for a historical side-effect call and its matching output
- **THEN** compact trimming drops the entire historical pair
- **AND** it does not retain only one member of that pair

#### Scenario: Side-effect call lacks a usable pair key

- **WHEN** an oversized compact input contains a recognised historical
  side-effect call without a non-empty `call_id`
- **THEN** compact trimming does not preserve that call as a side-effect anchor
- **AND** it does not emit an unpaired historical side-effect call upstream
