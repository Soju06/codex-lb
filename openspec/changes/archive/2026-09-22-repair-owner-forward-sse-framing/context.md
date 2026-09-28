# Owner-forward SSE compatibility

The owner-forward receiver now shares separator detection with the canonical
Responses client while retaining its own scheduler and timeout budget.
CRLF, CR-only, and mixed blank lines previously accumulated until EOF; several
JSON events could then reach downstream parsing as one invalid block.

For example, `data: {"type":"response.created"}\r\n\r\n` followed by a completed
event is delivered as two events even if the connection stays open. Invalid
bytes decode as U+FFFD, including at EOF. Valid UTF-8 split over network chunks
was already buffered safely and remains so. Replacing decoding errors does not
make malformed JSON valid; downstream JSON validation remains responsible for it.

No migration or operator setting is required. Ordinary bridge producers already
emit LF, so this is receiver compatibility hardening rather than evidence that
every normal bridge response currently fails. Event size policy is unchanged.
