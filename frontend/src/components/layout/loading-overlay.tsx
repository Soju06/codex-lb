import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export type LoadingOverlayProps = {
  visible: boolean;
  label?: string;
  className?: string;
};

export function LoadingOverlay({
  visible,
  label = "Loading...",
  className,
}: LoadingOverlayProps) {
  if (!visible) {
    return null;
  }

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm",
        className,
      )}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="flex items-center gap-2 rounded-lg border bg-card px-4 py-3 text-sm shadow-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{label}</span>
      </div>
    </div>
  );
}
