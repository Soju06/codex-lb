import { cn } from "@/lib/utils";

import { Spinner } from "@/components/ui/spinner";

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
        "fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm",
        className,
      )}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="flex items-center gap-2.5 rounded-xl border bg-card px-5 py-3.5 text-sm shadow-lg">
        <Spinner size="sm" />
        <span className="font-medium">{label}</span>
      </div>
    </div>
  );
}
