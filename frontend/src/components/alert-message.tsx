import { AlertCircle, CheckCircle2 } from "lucide-react";

import { cn } from "@/lib/utils";

export type AlertMessageProps = {
  variant: "error" | "success";
  className?: string;
  children: React.ReactNode;
};

const variantStyles: Record<AlertMessageProps["variant"], string> = {
  error: "bg-destructive/10 text-destructive border border-destructive/20",
  success: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20",
};

const variantIcons: Record<AlertMessageProps["variant"], React.ElementType> = {
  error: AlertCircle,
  success: CheckCircle2,
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
