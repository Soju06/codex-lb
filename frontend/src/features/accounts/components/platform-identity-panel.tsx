import type { AccountSummary } from "@/features/accounts/schemas";
import {
  formatCompactNumber,
  formatCurrency,
  formatProviderLabel,
  formatRouteFamilyLabel,
  formatTimeLong,
} from "@/utils/formatters";

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Not yet validated";
  }
  const formatted = formatTimeLong(value);
  if (formatted.date === "--") {
    return "Not yet validated";
  }
  return `${formatted.date} ${formatted.time}`;
}

function MetadataRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="space-y-1">
      <dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}

export type PlatformIdentityPanelProps = {
  account: AccountSummary;
};

export function PlatformIdentityPanel({ account }: PlatformIdentityPanelProps) {
  const requestUsage = account.requestUsage ?? null;
  const hasRequestUsage = (requestUsage?.requestCount ?? 0) > 0;
  const routeFamilies = account.eligibleRouteFamilies.length > 0
    ? account.eligibleRouteFamilies.map((routeFamily) => {
        if (routeFamily === "public_models_http") {
          return `Fallback ${formatRouteFamilyLabel(routeFamily)}`;
        }
        if (routeFamily === "public_responses_http") {
          return "Fallback stateless HTTP /v1/responses";
        }
        if (routeFamily === "backend_codex_http") {
          return "Fallback HTTP /backend-api/codex/models + stateless HTTP /backend-api/codex/responses";
        }
        return formatRouteFamilyLabel(routeFamily);
      }).join(", ")
    : "None";
  const responsesFallbackEnabled = account.eligibleRouteFamilies.includes("public_responses_http");
  const backendCodexFallbackEnabled = account.eligibleRouteFamilies.includes("backend_codex_http");

  return (
    <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        OpenAI Platform
      </h3>

      <dl className="grid gap-4 md:grid-cols-2">
        <MetadataRow label="Provider" value={`${formatProviderLabel(account.providerKind)} API key`} />
        <MetadataRow label="Routing subject" value={account.routingSubjectId || "Not assigned"} />
        <MetadataRow label="Eligible fallback routes" value={routeFamilies} />
        <MetadataRow label="Organization" value={account.organization || "Default"} />
        <MetadataRow label="Project" value={account.project || "Default"} />
        <MetadataRow label="Last validated" value={formatTimestamp(account.lastValidatedAt)} />
        <MetadataRow label="Last auth failure" value={account.lastAuthFailureReason || "None"} />
      </dl>

      <div className="rounded-md border bg-background/60 px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Fallback only. ChatGPT accounts stay primary, and this key is used only when the compatible ChatGPT pool is
          unhealthy under the configured primary or secondary usage drain thresholds.
        </p>
        {responsesFallbackEnabled ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Public Responses fallback covers stateless HTTP <code>/v1/responses</code> only.
          </p>
        ) : null}
        {backendCodexFallbackEnabled ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Codex HTTP fallback covers <code>/backend-api/codex/models</code> plus stateless HTTP{" "}
            <code>/backend-api/codex/responses</code> only.
          </p>
        ) : null}
        {responsesFallbackEnabled || backendCodexFallbackEnabled ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Compact, websocket, and continuity-bound requests stay on ChatGPT.
          </p>
        ) : null}
      </div>

      <div className="rounded-md border bg-background/60 px-3 py-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Request logs total
        </p>
        {hasRequestUsage ? (
          <p className="mt-1 text-xs tabular-nums text-muted-foreground">
            {formatCompactNumber(requestUsage?.totalTokens)} tok |{" "}
            {formatCompactNumber(requestUsage?.cachedInputTokens)} cached |{" "}
            {formatCompactNumber(requestUsage?.requestCount)} req |{" "}
            {formatCurrency(requestUsage?.totalCostUsd)}
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">No request usage yet.</p>
        )}
      </div>
    </div>
  );
}
