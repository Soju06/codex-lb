# Windows parity decisions

The neutral class is "the local process cannot resolve or route to the upstream
host". A Windows code joins it only when it is the Windows form of an errno or
resolver failure that is already neutral on POSIX.

| Code | Name | POSIX equivalent | Decision |
| --- | --- | --- | --- |
| 1231 | `ERROR_NETWORK_UNREACHABLE` | `ENETUNREACH` | neutral |
| 1232 | `ERROR_HOST_UNREACHABLE` | `EHOSTUNREACH` | neutral |
| 10050, 10051, 10064, 10065 | `WSAENETDOWN`, `WSAENETUNREACH`, `WSAEHOSTDOWN`, `WSAEHOSTUNREACH` | same errno | already neutral, no change |
| 11001, 11002, 11003 | `WSAHOST_NOT_FOUND`, `WSATRY_AGAIN`, `WSANO_RECOVERY` | `EAI_NONAME`, `EAI_AGAIN`, `EAI_FAIL` | already neutral, no change |
| 64 | `ERROR_NETNAME_DELETED` | `ECONNRESET` | attributed |
| 121 | `ERROR_SEM_TIMEOUT` | `ETIMEDOUT` | attributed |
| 1225, 1236 | `ERROR_CONNECTION_REFUSED`, `ERROR_CONNECTION_ABORTED` | `ECONNREFUSED`, `ECONNABORTED` | attributed |

## Evidence

- CPython `PC/errmap.h` (`winerror_to_errno`) passes Winsock codes
  10000-11999 through as errno values, so `errno.ENETUNREACH` is 10051 on
  Windows and main already matches the WSA route codes. Codes 64, 121, 1231,
  and 1232 have no case and fall to `default: return EINVAL`.
- `Modules/overlapped.c` `Overlapped.getresult` raises the
  `GetOverlappedResult` Win32 error. `Lib/asyncio/windows_events.py`
  `finish_connect` re-raises it unchanged; `finish_socket_func` re-raises
  `ERROR_NETNAME_DELETED` as `ConnectionResetError`.
- CPython 3.13.4 on Windows 11, `ProactorEventLoop.sock_connect`:
  `0.1.2.3:443` and `240.0.0.1:443` raised winerror 1231 with errno 22;
  `224.0.0.1:443` raised winerror 1232 with errno 22; `192.0.2.1:443` and
  `10.255.255.1:443` raised winerror 121 with errno 22 (connect timeout to an
  unresponsive endpoint); `127.0.0.1:1` raised winerror 1225.

## Decision basis

The decision rests on POSIX parity alone. 1231/1232 can also be
per-destination, for example an ICMP unreachable for one IP. POSIX
`ENETUNREACH`/`EHOSTUNREACH` have the same ambiguity, and main already treats
them as neutral, so parity holds. 64/121 correspond to `ECONNRESET`/`ETIMEDOUT`,
which main keeps attributed, and no traceback ties them to host-wide loss.

## Known residual gap

aiohappyeyeballs 2.6.1 (`impl.py:141`) collapses multi-address connect failures
with differing `str()` into `OSError(first_errno, msg)`, which drops `winerror`,
so such a 1231/1232 failure stays `upstream_unavailable`. POSIX loses mixed
errnos the same way (`impl.py:149`), so parity holds; this is not a regression.

## Replay

Classification does not prove dispatch did not begin. Only an existing typed
connector exception can mark the failure pre-dispatch and allow a same-account
retry within the request deadline. A raw `OSError` retires the failed
generation for later callers and surfaces the current failure. Error message
text never grants provenance.
