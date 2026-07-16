import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

const sizeClasses = {
  sm: "h-4 w-4",
  md: "h-5 w-5",
} as const;

type SpinnerProps = {
  size?: keyof typeof sizeClasses;
  className?: string;
};

export function Spinner({ size = "md", className }: SpinnerProps) {
  return <Loader2 aria-hidden="true" className={cn(sizeClasses[size], "animate-spin text-primary", className)} />;
}

type SpinnerBlockProps = {
  label?: string;
  className?: string;
};

export function SpinnerBlock({ label, className }: SpinnerBlockProps) {
  const { t } = useTranslation();
  const labelText = label ?? t("common.loading");

  return (
    <output className={cn("flex flex-col items-center gap-3", className)}>
      <Spinner />
      <p className="text-sm text-muted-foreground">{labelText}</p>
    </output>
  );
}
