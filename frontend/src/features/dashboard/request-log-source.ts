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
 */
export const REQUEST_LOG_SOURCE_OVERFLOW = "subscription_overflow";
export const REQUEST_LOG_SOURCE_OVERFLOW_PINNED = "subscription_overflow_pinned";

export type RequestLogSourceKind = "overflow" | "overflowPinned";

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
