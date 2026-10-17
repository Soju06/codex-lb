# Design

## Measured target

Two independent direct Codex 0.151.0 captures produced the same ordered initial
SETTINGS and connection control profile:

- `ENABLE_PUSH=0`
- `INITIAL_WINDOW_SIZE=2097152`
- `MAX_FRAME_SIZE=16384`
- `MAX_HEADER_LIST_SIZE=16384`
- connection `WINDOW_UPDATE=5177345`, yielding a 5242880-byte receive window

The native helper used `INITIAL_WINDOW_SIZE=65535` and no initial connection
WINDOW_UPDATE. Explicit reqwest HTTP/2 window settings reproduce the measured
target; adaptive flow control is disabled because it overrides explicit
windows and creates a different startup profile.

The comparison excludes a SETTINGS frame with the ACK flag from the stable
connection-control projection. That frame is a mandatory reaction to the
observer's server SETTINGS and may cross the first request HEADERS depending on
server response timing; it is not a client-selected startup capability. The
initial non-ACK SETTINGS and WINDOW_UPDATE remain exact gates.

## Header replacement

Python dictionaries preserve insertion order when an existing key is assigned.
The Responses path will therefore locate a case-insensitive existing singleton
key and assign through that exact spelling. Only absent fields are appended.
Duplicate case variants are collapsed without moving the first occurrence.
Native account-id replacement follows the same rule. Non-native fingerprint
normalization retains its existing canonical behavior.

Model discovery is constructed directly in observed order: authorization,
account id when present, accept, originator, and User-Agent. The client version
continues to be carried in the query parameter and User-Agent; the standalone
`version` header is removed because repeated direct captures omit it.

## Verification boundary

Unit tests prove deterministic builder constants and ordered header maps. The
same TLS controlled origin then compares A′, A, and C. Stable dimensions must
match; HPACK digests and body sizes remain informational and credential/body
values remain uncaptured.
