export type DashboardAccountStatus = "active" | "paused" | "limited" | "exceeded" | "reauth" | "deactivated";

export function quotaBarColor(percent: number): string {
  if (percent >= 70) return "bg-emerald-500";
  if (percent >= 30) return "bg-amber-500";
  return "bg-red-500";
}

export function quotaBarTrack(percent: number): string {
  if (percent >= 70) return "bg-emerald-500/15";
  if (percent >= 30) return "bg-amber-500/15";
  return "bg-red-500/15";
}

export function normalizeStatus(status: string): DashboardAccountStatus {
  if (status === "paused") {
    return "paused";
  }
  if (status === "rate_limited") {
    return "limited";
  }
  if (status === "quota_exceeded") {
    return "exceeded";
  }
  if (status === "reauth_required") {
    return "reauth";
  }
  if (status === "deactivated") {
    return "deactivated";
  }
  return "active";
}

function isHardBlockedStatus(status: string): boolean {
  const normalized = normalizeStatus(status);
  return normalized === "paused" || normalized === "reauth" || normalized === "deactivated";
}

export function isAccountAssignmentSelectable(status: string): boolean {
  return !isHardBlockedStatus(status);
}

export function isSingleAccountRoutingSelectable(status: string): boolean {
  return !isHardBlockedStatus(status);
}
