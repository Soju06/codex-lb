# File-finalize poll replay guard

The upstream file-finalize endpoint can return `retry`, causing one downstream call to issue multiple upstream POST polls. A failure on an individual later POST may be safe to retry as an individual request, but the proxy account failover restarts the *entire* operation on a different account. Once the first poll has returned, that account has observed the file identifier; crossing accounts is no longer justified by the later request's pre-dispatch failure.

For example, an unpinned file receives `retry` from account A, then the second poll's proxy connection to A is refused. Return the transport error instead of starting a new finalization operation on B. If the first connection to A is refused before any poll returns, existing account failover may still use B. An owner-pinned file remains strict even on the first poll. The direct transport's existing error handling and polling budget remain unchanged.
