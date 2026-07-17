import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { Spinner } from "@/components/ui/spinner";

export type LoadingOverlayProps = {
  visible: boolean;
  label?: string;
  className?: string;
};

export function LoadingOverlay({
  visible,
  label,
  className,
}: LoadingOverlayProps) {
  const { t } = useTranslation();
  const labelText = label ?? t("common.loading");

  if (!visible) {
    return null;
  }

  return (
    <output
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm",
        className,
      )}
      aria-live="polite"
      aria-label={labelText}
    >
      <div className="flex items-center gap-2.5 rounded-xl border bg-card px-5 py-3.5 text-sm shadow-lg">
        <Spinner size="sm" />
        <span className="font-medium">{labelText}</span>
      </div>
    </output>
  );
}
