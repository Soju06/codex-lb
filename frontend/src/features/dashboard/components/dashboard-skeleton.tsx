import { Skeleton } from "@/components/ui/skeleton";

export function DashboardSkeleton() {
  return (
    <div className="space-y-8">
      {/* Stats grid */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-xl border bg-card p-4">
            <div className="flex items-center justify-between">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-8 w-8 rounded-lg" />
            </div>
            <div className="mt-1">
              <Skeleton className="h-7 w-24" />
              <Skeleton className="mt-1 h-3 w-32" />
            </div>
            <Skeleton className="mt-1 h-10 w-full" />
          </div>
        ))}
      </div>

      {/* Usage donuts */}
      <div className="grid gap-4 lg:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="rounded-xl border bg-card p-5">
            <div className="mb-5 space-y-1">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-3 w-20" />
            </div>
            <div className="flex items-center gap-6">
              <Skeleton className="h-36 w-36 shrink-0 rounded-full" />
              <div className="flex-1 space-y-2.5">
                {Array.from({ length: 5 }).map((_, j) => (
                  <div key={j} className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Skeleton className="h-2.5 w-2.5 rounded-full" />
                      <Skeleton className="h-3 w-28" />
                    </div>
                    <Skeleton className="h-3 w-10" />
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Accounts section */}
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-5 w-24" />
          <div className="h-px flex-1 bg-border" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-xl border bg-card p-4">
              {/* Header */}
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1.5">
                  <Skeleton className="h-4 w-36" />
                </div>
                <Skeleton className="h-5 w-14 rounded-full" />
              </div>
              {/* Quota bars - horizontal 2 columns */}
              <div className="mt-3.5 grid grid-cols-2 gap-3">
                {Array.from({ length: 2 }).map((_, j) => (
                  <div key={j} className="space-y-1">
                    <div className="flex items-center justify-between">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-3 w-10" />
                    </div>
                    <Skeleton className="h-1.5 w-full rounded-full" />
                    <div className="flex items-center gap-1">
                      <Skeleton className="h-3 w-3 rounded-sm" />
                      <Skeleton className="h-3 w-20" />
                    </div>
                  </div>
                ))}
              </div>
              {/* Actions */}
              <div className="mt-3 border-t pt-3">
                <Skeleton className="h-7 w-16 rounded-lg" />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Request logs section */}
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-5 w-28" />
          <div className="h-px flex-1 bg-border" />
        </div>

        {/* Filters */}
        <div className="space-y-2 rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <Skeleton className="h-8 flex-1 rounded-md" />
            <Skeleton className="h-8 w-32 rounded-md" />
          </div>
          <div className="flex items-center gap-2">
            <Skeleton className="h-8 w-24 rounded-md" />
            <Skeleton className="h-8 w-20 rounded-md" />
            <Skeleton className="h-8 w-22 rounded-md" />
            <Skeleton className="h-8 w-16 rounded-md" />
          </div>
        </div>

        {/* Table */}
        <div className="rounded-xl border bg-card">
          <div className="overflow-x-auto">
            <div className="min-w-[800px]">
              {/* Table header */}
              <div className="flex items-center gap-4 border-b px-4 py-2.5">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-3 w-20 flex-1" />
                <Skeleton className="h-3 w-16 flex-1" />
                <Skeleton className="h-3 w-14" />
                <Skeleton className="h-3 w-14" />
                <Skeleton className="h-3 w-10" />
                <Skeleton className="h-3 w-14" />
              </div>
              {/* Table rows */}
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-start gap-4 border-b px-4 py-3 last:border-b-0">
                  <div className="w-16 space-y-1">
                    <Skeleton className="h-3.5 w-14" />
                    <Skeleton className="h-3 w-16" />
                  </div>
                  <Skeleton className="h-3.5 w-24 flex-1" />
                  <Skeleton className="h-3.5 w-28 flex-1 font-mono" />
                  <Skeleton className="h-5 w-14 rounded-full" />
                  <div className="w-14 space-y-1">
                    <Skeleton className="h-3.5 w-12" />
                    <Skeleton className="h-3 w-14" />
                  </div>
                  <Skeleton className="h-3.5 w-10" />
                  <Skeleton className="h-3.5 w-14" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Pagination */}
        <div className="flex justify-end">
          <div className="flex items-center gap-2">
            <Skeleton className="h-3 w-8" />
            <Skeleton className="h-8 w-20 rounded-md" />
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-8 w-8 rounded-md" />
            <Skeleton className="h-8 w-8 rounded-md" />
            <Skeleton className="h-8 w-8 rounded-md" />
            <Skeleton className="h-8 w-8 rounded-md" />
          </div>
        </div>
      </div>
    </div>
  );
}
