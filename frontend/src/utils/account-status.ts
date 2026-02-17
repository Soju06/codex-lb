export type DashboardAccountStatus = "active" | "paused" | "limited" | "exceeded" | "deactivated";

export const STATUS_DOT: Record<DashboardAccountStatus, string> = {
  active: "bg-emerald-500",
  paused: "bg-amber-500",
  limited: "bg-orange-500",
  exceeded: "bg-red-500",
  deactivated: "bg-zinc-400",
};

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
  if (status === "deactivated") {
    return "deactivated";
  }
  return "active";
}
