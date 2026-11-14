# Context
Received peer 1009 is distinct from a locally sent 1009 with no peer close. Only the former is changed. Example: a send raising ConnectionClosedError with received Close(1009) returns payload_too_large. Existing settlement and no-replay protections remain. B/C and Native are outside this repair. Offline success is not production recovery.
