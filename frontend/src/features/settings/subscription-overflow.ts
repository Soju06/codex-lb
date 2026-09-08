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

// The drain deadline is only meaningful while it lies ahead; the backend
// leaves an elapsed deadline in place until the next designation.
export function isSubscriptionOverflowDraining(drainUntil: string | null | undefined, now = Date.now()): boolean {
  if (!drainUntil) {
    return false;
  }
  const deadline = Date.parse(drainUntil);
  return Number.isFinite(deadline) && deadline > now;
}
