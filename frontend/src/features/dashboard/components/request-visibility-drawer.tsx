import { EyeOff, Shield, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { SpinnerBlock } from "@/components/ui/spinner";
import { useRequestLogVisibility } from "@/features/dashboard/hooks/use-request-log-visibility";
import type { RequestLog } from "@/features/dashboard/schemas";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { useSettings } from "@/features/settings/hooks/use-settings";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import {
  formatCompactNumber,
  formatCurrency,
  formatModelLabel,
  formatTimeLong,
} from "@/utils/formatters";

const FAST_BADGE_CLASS =
  "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:border-amber-400/30 dark:text-amber-300";
const TEMPORARY_OPTIONS = [
  { minutes: 15, label: "15 minutes" },
  { minutes: 60, label: "1 hour" },
  { minutes: 240, label: "4 hours" },
] as const;

type RequestVisibilityDrawerProps = {
  request: RequestLog | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function RequestVisibilityDrawer({ request, open, onOpenChange }: RequestVisibilityDrawerProps) {
  const requestId = request?.requestId ?? null;
  const visibilityQuery = useRequestLogVisibility(requestId, open);
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const [temporaryDuration, setTemporaryDuration] = useState<string>(String(TEMPORARY_OPTIONS[1].minutes));

  const visibleServiceTier = request ? request.actualServiceTier ?? request.serviceTier : null;
  const showRequestedTier = !!request && !!request.requestedServiceTier && request.requestedServiceTier !== visibleServiceTier;
  const time = request ? formatTimeLong(request.requestedAt) : null;
  const bodyObject =
    visibilityQuery.data?.body && typeof visibilityQuery.data.body === "object" && !Array.isArray(visibilityQuery.data.body)
      ? (visibilityQuery.data.body as Record<string, unknown>)
      : null;
  const reasoningObject =
    bodyObject?.reasoning && typeof bodyObject.reasoning === "object" && !Array.isArray(bodyObject.reasoning)
      ? (bodyObject.reasoning as Record<string, unknown>)
      : null;
  const capturedServiceTier = typeof bodyObject?.service_tier === "string" ? bodyObject.service_tier : null;
  const reasoningEffort =
    typeof reasoningObject?.effort === "string" ? reasoningObject.effort : (request?.reasoningEffort ?? null);
  const reasoningSummary = typeof reasoningObject?.summary === "string" ? reasoningObject.summary : null;

  const headerText = useMemo(() => prettyJson(visibilityQuery.data?.headers ?? {}), [visibilityQuery.data?.headers]);
  const bodyText = useMemo(() => prettyJson(visibilityQuery.data?.body ?? null), [visibilityQuery.data?.body]);
  const settings = settingsQuery.data;
  const canShowEnableCta = !!settings && !settings.requestVisibilityEnabled;

  const enableVisibility = async (mode: "persistent" | "temporary") => {
    if (!settings) {
      return;
    }
    await updateSettingsMutation.mutateAsync(
      buildSettingsUpdateRequest(settings, {
        requestVisibilityMode: mode,
        ...(mode === "temporary"
          ? { requestVisibilityDurationMinutes: Number.parseInt(temporaryDuration, 10) }
          : {}),
      }),
    );
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader className="border-b pb-4">
          <SheetTitle>Request visibility</SheetTitle>
          <SheetDescription>
            Selected request headers plus request payload details captured for dashboard troubleshooting.
          </SheetDescription>
        </SheetHeader>

        {request ? (
          <div className="space-y-6 p-4">
            <section className="rounded-xl border bg-muted/20 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{request.requestId}</h3>
                {visibleServiceTier === "priority" ? (
                  <Badge variant="outline" className={FAST_BADGE_CLASS}>
                    Fast
                  </Badge>
                ) : null}
                <Badge variant="outline">{REQUEST_STATUS_LABELS[request.status] ?? request.status}</Badge>
              </div>
              <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Model</div>
                  <div className="font-mono text-xs">
                    {formatModelLabel(request.model, request.reasoningEffort)}
                  </div>
                  {showRequestedTier ? (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Requested {request.requestedServiceTier}
                    </div>
                  ) : null}
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Requested</div>
                  <div>{time?.time}</div>
                  <div className="text-xs text-muted-foreground">{time?.date}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Tokens</div>
                  <div>{formatCompactNumber(request.tokens)}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Cost</div>
                  <div>{formatCurrency(request.costUsd)}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Requested tier</div>
                  <div>{request.requestedServiceTier ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Effective tier</div>
                  <div>{visibleServiceTier ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Reasoning effort</div>
                  <div>{reasoningEffort ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Reasoning summary</div>
                  <div className="break-words">{reasoningSummary ?? "—"}</div>
                </div>
              </div>
            </section>

            {request.errorMessage || request.errorCode ? (
              <section className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
                <div className="mb-2 flex items-center gap-2">
                  <TriangleAlert className="h-4 w-4 text-red-500" aria-hidden="true" />
                  <h3 className="text-sm font-semibold">Error</h3>
                </div>
                <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-muted-foreground">
                  {request.errorMessage ?? request.errorCode}
                </p>
              </section>
            ) : null}

            {visibilityQuery.data?.captured ? (
              <section className="rounded-xl border p-4">
                <div className="mb-3 flex items-center gap-2">
                  <h3 className="text-sm font-semibold">Captured request metadata</h3>
                  {capturedServiceTier ? <Badge variant="outline">{capturedServiceTier}</Badge> : null}
                  {visibilityQuery.data.truncated ? <Badge variant="outline">Truncated body</Badge> : null}
                </div>
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-muted-foreground">Captured service tier</dt>
                    <dd>{capturedServiceTier ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-muted-foreground">Reasoning summary</dt>
                    <dd className="break-words">{reasoningSummary ?? "—"}</dd>
                  </div>
                </dl>
              </section>
            ) : null}

            {visibilityQuery.isLoading && !visibilityQuery.data ? (
              <SpinnerBlock className="py-10" label="Loading request visibility..." />
            ) : null}

            {visibilityQuery.isError ? (
              <EmptyState
                icon={TriangleAlert}
                title="Couldn't load request visibility"
                description={visibilityQuery.error instanceof Error ? visibilityQuery.error.message : "Request visibility could not be loaded."}
              />
            ) : null}

            {visibilityQuery.data && !visibilityQuery.data.captured ? (
              <div className="space-y-4">
                {canShowEnableCta ? (
                  <section className="rounded-xl border p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold">Enable capture for future requests</h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          Applies only to new traffic. Secret-like fields are redacted, selected headers and other payload fields may remain visible, and this row will never be backfilled.
                        </p>
                      </div>
                      <Badge variant="outline">Currently off</Badge>
                    </div>

                    <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Shield className="h-3.5 w-3.5" aria-hidden="true" />
                        Future captures exclude session-id header keys, redact secret-like fields, and may retain selected header values plus other payload fields.
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={updateSettingsMutation.isPending || settingsQuery.isLoading}
                          onClick={() => void enableVisibility("persistent")}
                        >
                          Enable always
                        </Button>
                        <Select value={temporaryDuration} onValueChange={setTemporaryDuration}>
                          <SelectTrigger
                            className="h-8 w-32 text-xs"
                            disabled={updateSettingsMutation.isPending || settingsQuery.isLoading}
                            aria-label="Drawer temporary visibility duration"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent align="end">
                            {TEMPORARY_OPTIONS.map((option) => (
                              <SelectItem key={option.minutes} value={String(option.minutes)}>
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button
                          type="button"
                          size="sm"
                          disabled={updateSettingsMutation.isPending || settingsQuery.isLoading}
                          onClick={() => void enableVisibility("temporary")}
                        >
                          Enable temporarily
                        </Button>
                      </div>
                    </div>
                  </section>
                ) : null}

                <EmptyState
                  icon={EyeOff}
                  title="Request visibility unavailable"
                  description={
                    canShowEnableCta
                      ? "This row was not captured. You can enable request visibility for future requests above."
                      : "This request was not captured. Capture may have been disabled, unsupported, or unavailable for this row."
                  }
                />
              </div>
            ) : null}

            {visibilityQuery.data?.captured ? (
              <div className="space-y-4">
                <section className="rounded-xl border p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <h3 className="text-sm font-semibold">Request headers</h3>
                    {visibilityQuery.data.truncated ? <Badge variant="outline">Truncated</Badge> : null}
                  </div>
                  <pre className="overflow-x-auto rounded-lg bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap break-all">
                    {headerText}
                  </pre>
                </section>

                <section className="rounded-xl border p-4">
                  <h3 className="mb-3 text-sm font-semibold">Request body</h3>
                  <pre className="overflow-x-auto rounded-lg bg-muted/40 p-3 text-xs leading-relaxed whitespace-pre-wrap break-all">
                    {bodyText}
                  </pre>
                </section>
              </div>
            ) : null}
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
