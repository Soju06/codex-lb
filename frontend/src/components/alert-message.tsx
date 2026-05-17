import { AlertCircle, CheckCircle2, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type AlertMessageProps = {
  variant: "error" | "success" | "warning";
  className?: string;
  children: React.ReactNode;
};

const variantStyles: Record<AlertMessageProps["variant"], string> = {
  error: "bg-destructive/10 text-destructive border border-destructive/20",
  success: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20",
  warning: "border border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

const variantIcons: Record<AlertMessageProps["variant"], React.ElementType> = {
  error: AlertCircle,
  success: CheckCircle2,
  warning: TriangleAlert,
};

export function AlertMessage({ variant, className, children }: AlertMessageProps) {
  const Icon = variantIcons[variant];
  return (
    <div className={cn("flex items-start gap-2.5 rounded-lg px-3 py-2 text-xs font-medium", variantStyles[variant], className)}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}
