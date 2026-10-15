## 1. Distinguish a local refusal from an upstream transport failure

- [x] 1.1 Reproduce the empty `200` on an unchanged tree: a native Codex bridge
      turn whose proxy-injected anchor upstream denies, whose recovery retry the
      before-dispatch fence refuses, delivers zero bytes and no terminal event.
- [x] 1.2 Add `local_pre_dispatch_refusal` to `ProxyResponseError` and set it on
      the denied-anchor fence, leaving the status, code, message, and
      `continuity_fail_closed` record unchanged.
- [x] 1.3 Copy the flag through `_OwnerForwardRequestError`'s field rebuild.
- [x] 1.4 Carry the flag across the internal bridge owner forward: the owner
      marks its error response, the relay reads the marker back onto the error
      it rebuilds from the owner's status and body, and a bare non-200 is not
      taken as proof of a local refusal.

## 2. Deliver the refusal on both downstream phases

- [x] 2.1 Return the ordinary HTTP error response when the startup probe catches
      a flagged refusal before the downstream response commits.
- [x] 2.2 Yield an unmarked terminal `response.failed` when the response has
      already committed, so the native normalizer does not convert the terminal
      back into an abort.

## 3. Coverage

- [x] 3.1 Bridge-route regression for the pre-commit phase (HTTP 502 with the
      `stream_incomplete` envelope), the post-commit phase (terminal
      `response.failed` plus `[DONE]`, unmarked, no escaped exception), and the
      unchanged non-native 502 JSON contract. Each asserts the
      `continuity_fail_closed reason=denied_proxy_anchor_before_dispatch`
      record, so a scenario that drifted onto the plain upstream denial fails.
- [x] 3.2 Stream-shaping unit coverage that the flagged error yields one unmarked
      terminal and the same error without the flag still ends the native stream
      without one.
- [x] 3.3 Owner-forward regression on both halves of the hop: the forwarded
      route's 502 carries the marker, and a native turn whose owner answers with
      a marked 502 receives that envelope on the origin instead of a zero-byte
      committed 200. Relay-level coverage that an unmarked non-200 stays
      unflagged.
- [x] 3.4 Pass the bridge, owner-forwarding, proxy-utils, and transient-retry
      suites, the static checks, and strict OpenSpec validation.
