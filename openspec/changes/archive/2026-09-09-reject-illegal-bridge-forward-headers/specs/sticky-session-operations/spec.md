## ADDED Requirements

### Requirement: Owner forwarding rejects illegal reconstructed header metadata

Owner-forwarded HTTP bridge requests MUST validate reconstructed bridge
metadata before building signatures or posting headers to another owner.
Metadata values that become signed bridge headers MUST NOT contain illegal HTTP
header control characters. If original affinity, downstream turn-state,
file-owner, client-IP, origin/target instance, or reservation metadata contains
such a character, the proxy MUST fail closed with the structured
`bridge_forward_invalid` error instead of sending the owner request. Ordinary
client headers with illegal HTTP control characters MUST be omitted from the
forwarded header map.

#### Scenario: Unsafe reservation metadata fails closed

- **GIVEN** an owner-forward request carries API-key reservation metadata
- **AND** one reservation field contains an illegal HTTP header control
  character
- **WHEN** the origin builds the owner-forward request
- **THEN** it returns `bridge_forward_invalid`
- **AND** it does not omit only the reservation headers while keeping the owner
  as reservation-settlement authority

#### Scenario: Unsafe client header is omitted

- **GIVEN** an owner-forward request includes an ordinary client header with an
  illegal HTTP header control character
- **WHEN** the origin builds the owner-forward request
- **THEN** that client header is not forwarded
- **AND** the signed bridge-forward metadata remains valid
