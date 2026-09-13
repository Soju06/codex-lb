/**
 * The closed set of `request_logs.source` values that mean "subscription
 * overflow" (#2123 WP-G).
 *
 * `source` is a plain nullable string on the wire — the proxy also writes
 * `limit_warmup` and `warmup_probe`, and most rows carry `null`. Only the two
 * values below are attributed in the UI, and anything else maps to `null` so a
 * future source value renders exactly as it does today.
 *
 * Anchor dispatches (SDK `previous_response_id` chains) share the pinned label
 * with thread pins, so the distinction is strictly two-way: an "anchor" chip
 * cannot be derived from a row.
 *
 * The literals below must stay equal to the `REQUEST_LOG_SOURCE_*` constants in
 * `app/modules/proxy/overflow.py`, which is the authority.
 * `tests/unit/test_request_log_source_parity.py` fails when either side drifts,
 * and `request-log-source.test.ts` keeps the labels for every kind present in
 * every locale.
 */
export const REQUEST_LOG_SOURCE_OVERFLOW = "subscription_overflow";
export const REQUEST_LOG_SOURCE_OVERFLOW_PINNED = "subscription_overflow_pinned";

/** Every chip/dialog label kind, in the same order as the filter values. */
export const REQUEST_LOG_SOURCE_KINDS = ["overflow", "overflowPinned"] as const;

export type RequestLogSourceKind = (typeof REQUEST_LOG_SOURCE_KINDS)[number];

/** The two values the request-log `source` filter offers, in menu order. */
export const REQUEST_LOG_SOURCE_FILTER_VALUES: readonly [string, string] = [
  REQUEST_LOG_SOURCE_OVERFLOW,
  REQUEST_LOG_SOURCE_OVERFLOW_PINNED,
];

/** `null` for every non-overflow row (null, `limit_warmup`, `warmup_probe`, future values). */
export function requestLogSourceKind(source: string | null | undefined): RequestLogSourceKind | null {
  switch (source) {
    case REQUEST_LOG_SOURCE_OVERFLOW:
      return "overflow";
    case REQUEST_LOG_SOURCE_OVERFLOW_PINNED:
      return "overflowPinned";
    default:
      return null;
  }
}
