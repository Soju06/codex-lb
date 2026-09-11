import type { ModelSource } from "@/features/model-sources/schemas";

// Radix Select cannot represent "no value" with an empty string, so the "Off"
// option carries a sentinel that is mapped back to null on save.
export const SUBSCRIPTION_OVERFLOW_OFF_VALUE = "__off__";

// Mirrors the backend eligibility rule (validate_overflow_source): only an
// OpenAI-compatible source that speaks the Responses API can be designated.
// Enabled-ness is deliberately not part of it: disabling is a kill switch.
export function isSubscriptionOverflowEligibleSource(source: ModelSource): boolean {
  return source.kind === "openai_compatible" && source.supportsResponses;
}

// Pinned conversations keep resolving only until `subscriptionOverflowPinsExpireBy`
// (the clear time plus the 7-day pin idle limit). The 29-day drain deadline the
// API also reports runs 22 days longer purely so expired pins stay answerable, so
// the notice is gated on the expiry date, which the backend leaves in place until
// the next designation.
export function isSubscriptionOverflowDraining(pinsExpireBy: string | null | undefined, now = Date.now()): boolean {
  if (!pinsExpireBy) {
    return false;
  }
  const expiry = Date.parse(pinsExpireBy);
  return Number.isFinite(expiry) && expiry > now;
}
