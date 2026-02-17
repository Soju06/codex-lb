import type { LucideIcon } from "lucide-react";

export type EmptyStateProps = {
  icon: LucideIcon;
  title: string;
  description?: string;
};

export function EmptyState({ icon: Icon, title, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border/60 p-10 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border bg-muted/50">
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium text-muted-foreground">{title}</p>
        {description ? <p className="text-xs text-muted-foreground/70">{description}</p> : null}
      </div>
    </div>
  );
}
