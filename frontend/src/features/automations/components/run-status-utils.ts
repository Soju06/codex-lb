import type { AutomationRunStatus } from "@/features/automations/schemas";

function statusVariant(
  status: AutomationRunStatus,
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "success":
      return "default";
    case "partial":
      return "secondary";
    case "failed":
      return "destructive";
    case "running":
      return "outline";
  }
}

export function formatRunStatusLabel(
  value: AutomationRunStatus,
  pendingAccounts?: number | null,
): string {
  if (value === "running" && (pendingAccounts ?? 0) > 0) {
    return "in progress";
  }
  return value;
}

export function runStatusVariant(
  value: AutomationRunStatus,
): "default" | "secondary" | "destructive" | "outline" {
  return statusVariant(value);
}

export function accountStateBadgeVariant(
  value: "pending" | AutomationRunStatus,
): "default" | "secondary" | "destructive" | "outline" {
  if (value === "pending") {
    return "outline";
  }
  return statusVariant(value);
}

