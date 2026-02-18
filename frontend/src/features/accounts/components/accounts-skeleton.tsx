import { Skeleton } from "@/components/ui/skeleton";

export function AccountsSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
      {/* Left: account list */}
      <div className="rounded-xl border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Skeleton className="h-8 flex-1 rounded-md" />
          <Skeleton className="h-8 w-32 rounded-md" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-8 flex-1 rounded-md" />
          <Skeleton className="h-8 flex-1 rounded-md" />
        </div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-2.5 rounded-lg px-3 py-2.5">
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-20" />
            </div>
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
        ))}
      </div>

      {/* Right: account detail */}
      <div className="rounded-xl border bg-card p-5 space-y-4">
        <div className="space-y-1.5">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-48" />
        </div>

        {/* Usage panel skeleton */}
        <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
          <Skeleton className="h-3 w-12" />
          {/* Quota rows - horizontal 2 columns */}
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Skeleton className="h-3 w-28" />
                  <Skeleton className="h-3 w-10" />
                </div>
                <Skeleton className="h-1.5 w-full rounded-full" />
                <div className="flex items-center gap-1.5">
                  <Skeleton className="h-3 w-3 rounded-sm" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
            ))}
          </div>
          {/* Trend chart placeholder */}
          <div className="pt-3">
            <div className="mb-2 flex items-center justify-between">
              <Skeleton className="h-3 w-20" />
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <Skeleton className="h-2 w-2 rounded-full" />
                  <Skeleton className="h-2.5 w-12" />
                </div>
                <div className="flex items-center gap-1.5">
                  <Skeleton className="h-2 w-2 rounded-full" />
                  <Skeleton className="h-2.5 w-16" />
                </div>
              </div>
            </div>
            <Skeleton className="h-24 w-full rounded-md" />
          </div>
        </div>

        {/* Token info skeleton */}
        <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
          <Skeleton className="h-3 w-20" />
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-20" />
            </div>
          ))}
        </div>

        {/* Actions skeleton - up to 3 buttons */}
        <div className="flex flex-wrap gap-2 border-t pt-4">
          <Skeleton className="h-8 w-20 rounded-md" />
          <Skeleton className="h-8 w-28 rounded-md" />
          <Skeleton className="h-8 w-20 rounded-md" />
        </div>
      </div>
    </div>
  );
}
