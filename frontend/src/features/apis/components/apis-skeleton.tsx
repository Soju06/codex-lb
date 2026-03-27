import { Skeleton } from "@/components/ui/skeleton";

export function ApisSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
      <div className="rounded-xl border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Skeleton className="h-8 flex-1 rounded-md" />
          <Skeleton className="h-8 w-32 rounded-md" />
        </div>
        <Skeleton className="h-8 w-full rounded-md" />
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={`list-${i}`} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5">
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-20" />
            </div>
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
        ))}
      </div>

      <div className="rounded-xl border bg-card p-5 space-y-4">
        <div className="flex items-start justify-between">
          <div className="space-y-1.5">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-3 w-32" />
          </div>
          <Skeleton className="h-8 w-8 rounded-md" />
        </div>

        <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
          <Skeleton className="h-3 w-20" />
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={`info-${i}`} className="flex items-center justify-between">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>

        <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
          <div className="mb-2 flex items-center justify-between">
            <Skeleton className="h-3 w-20" />
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <Skeleton className="h-2 w-2 rounded-full" />
                <Skeleton className="h-2.5 w-12" />
              </div>
              <div className="flex items-center gap-1.5">
                <Skeleton className="h-2 w-2 rounded-full" />
                <Skeleton className="h-2.5 w-12" />
              </div>
            </div>
          </div>
          <Skeleton className="h-48 w-full rounded-md" />
        </div>
      </div>
    </div>
  );
}
