/**
 * True when `label` matches `email` exactly — the label was derived from an email address.
 * Avoids false positives from display names that happen to contain "@".
 */
export function isEmailLabel(label: string | null | undefined, email: string | null | undefined): boolean {
  return !!label && !!email && label === email;
}
