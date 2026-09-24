# Context: reuse the shared refresh policy in Auth Guardian

## Purpose

Auth Guardian exists so accounts that receive no traffic, especially paused
accounts, still get periodic opportunities to rotate valid refresh credentials.
That purpose does not require a second definition of when credentials are
stale. Request preflight already owns that decision in `should_refresh()`.

## Decision

`should_refresh(last_refresh, now)` is the sole age predicate for both request
preflight and Auth Guardian. The guardian keeps its six-hour scan cadence and
passes its injected wall clock to the shared predicate. Once an eligible
account crosses the shared eight-day window, the next leader-gated scan
considers it. Admission still excludes active failure backoff and is limited to
the 100 accounts with the oldest `last_refresh`, so batch pressure can defer an
account through later scans. After a fresh per-account recheck confirms an
admitted account is still due, the worker uses the existing serialized
`force=True` exchange path.

The guardian-specific max-age constant and constructor field are deleted. They
are not retained as test seams because retaining them would preserve two policy
surfaces. Candidate selection and the fresh per-account recheck both use the
shared policy; the forced exchange is reachable only after that second check.
Tests control time through the existing scheduler `now` seam and can monkeypatch
the shared refresh-policy constant to prove policy inheritance.

## Constraints

- `active` and `paused` accounts remain guardian-eligible; `reauth_required` and
  `deactivated` accounts remain excluded.
- A paused account remains paused after a successful credential rotation.
- Upstream 401 recovery still forces an immediate refresh independently of age.
- In-process singleflight, cross-replica refresh claims, fresh re-reads and
  compare-and-set persistence remain untouched.
- Access-token JWT expiry is not a substitute: it describes the access token,
  not the refresh token's undocumented idle lifetime.

## Failure modes

A deployment whose refresh tokens expire in less than the shared eight-day
window could discover that only when a request forces refresh. The repository
has no evidence for such a lifetime, while the existing request policy already
accepts the same window. Operators retain the Auth Guardian enable/disable
switch; this change deliberately adds no tuning setting.

## Example

An active or paused account successfully refreshed at 00:00 on Monday remains
ineligible on every guardian scan for the next eight days. After the shared
window is crossed, the first subsequent six-hour guardian scan selects it only
if it is admitted within the 100-account batch and is not in active failure
backoff; otherwise a later scan can select it. A request that receives an
upstream 401 before then still takes the existing immediate forced-refresh
path, limiting the practical impact of deferred proactive admission.
