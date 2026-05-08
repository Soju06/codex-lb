## MODIFIED Requirements

### Requirement: Peer fallback prevents request loops

The service MUST mark peer fallback attempts with a numeric fallback depth. The service MUST reject inbound peer-forwarded requests when the recorded depth is greater than or equal to `peer_fallback_max_hops`. The service MAY initiate another peer fallback for an inbound peer-forwarded request only while the recorded depth is below `peer_fallback_max_hops`, and the outbound fallback request MUST increment the recorded depth. The default `peer_fallback_max_hops = 1` MUST preserve single-hop behavior.

#### Scenario: Peer-forwarded request below hop limit may fallback again

- **GIVEN** `peer_fallback_max_hops` is `2`
- **AND** an inbound request contains fallback depth `1`
- **WHEN** that request cannot complete before downstream-visible output starts
- **THEN** the receiving peer may forward the request to another configured peer
- **AND** the forwarded request contains fallback depth `2`

#### Scenario: Peer-forwarded request at hop limit is not forwarded

- **GIVEN** `peer_fallback_max_hops` is `2`
- **AND** an inbound request contains fallback depth `2`
- **WHEN** that request cannot complete before downstream-visible output starts
- **THEN** the receiving peer returns a local failure for that attempt
- **AND** it does not forward the request to another peer
