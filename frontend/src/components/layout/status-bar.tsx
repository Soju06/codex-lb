import { formatTimeLong } from "@/utils/formatters";

export type StatusBarProps = {
  lastSyncAt?: string | null;
  routingStrategy?: string | null;
  backendPath?: string;
};

export function StatusBar({
  lastSyncAt = null,
  routingStrategy = "usage_weighted",
  backendPath = "/api",
}: StatusBarProps) {
  const lastSync = formatTimeLong(lastSyncAt);

  return (
    <footer className="border-t bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
      <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-x-4 gap-y-1">
        <span>Last sync: {lastSync.time}</span>
        <span>Routing: {routingStrategy}</span>
        <span>Backend: {backendPath}</span>
      </div>
    </footer>
  );
}
