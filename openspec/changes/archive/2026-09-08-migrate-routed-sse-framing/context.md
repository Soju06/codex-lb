# Routed SSE migration boundary

`CodexClient` accepts typed `native_sse` options only with unbuffered response
consumption. This parameter is separate from HTTP client keyword arguments;
neither native request serialization eligibility nor Python fallback sees it as
an upstream HTTP argument. Successful native responses retain their concrete
type instead of being hidden behind the raw-content compatibility adapter.

For example, an account-bound POST selects endpoint A. If its connection fails
with proven pre-dispatch provenance, Python may select endpoint B and submit
the same framing limits. The route trace records B and fallback use. Once a
response head has been returned, framing failure, idle timeout, or cancellation
does not re-enter endpoint selection or replay the POST. Rust reads and frames
only the selected attempt; Python retains all domain decisions.

HTTP errors and non-streaming JSON retain their raw-body interface. An absent
helper uses the existing Python transport and parser for the same resolved
endpoint, with no native-only keyword reaching aiohttp. The configured total
request budget and current routed header-wait behavior are unchanged. Body
idle deadlines and event byte limits are enforced by Rust on native attempts.

The migration reuses `http_sse_v1`; old installed helpers fail during the
existing mandatory negotiation. Compact/other buffered responses remain raw.
Shared direct/routed loopback tests prove byte limits before UTF-8 expansion,
partial-event activity, lifecycle errors, cancellation scope cleanup, and
isolation between requests in one worker.

A cancellation regression also exposed locally created routed sessions whose
asynchronous close was interrupted by an already cancelled scope. Response
cleanup already protects native request cancellation; the routed owner now
uses the existing deferred-cancellation helper to finish its client close,
then propagates cancellation. Borrowed clients stay owned by their caller.
