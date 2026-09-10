import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ModelSource } from "@/features/model-sources/schemas";
import { useSubscriptionOverflowPreflight } from "@/features/settings/hooks/use-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type {
  DashboardSettings,
  SettingsUpdateRequest,
  SubscriptionOverflowPreflight,
  SubscriptionOverflowPreflightModel,
} from "@/features/settings/schemas";
import {
  SUBSCRIPTION_OVERFLOW_OFF_VALUE,
  isSubscriptionOverflowDraining,
  isSubscriptionOverflowEligibleSource,
} from "@/features/settings/subscription-overflow";
import { cn } from "@/lib/utils";
import { formatDateTimeInline } from "@/utils/formatters";

const EMPTY_SOURCES: ModelSource[] = [];
const HELP_KEYS = ["trigger", "eligibility", "stickiness", "billing", "runtime"] as const;

export type SubscriptionOverflowSettingsProps = {
  settings: DashboardSettings;
  modelSources?: ModelSource[];
  modelSourcesLoading?: boolean;
  modelSourcesError?: boolean;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function SubscriptionOverflowSettings({
  settings,
  modelSources = EMPTY_SOURCES,
  modelSourcesLoading = false,
  modelSourcesError = false,
  busy,
  onSave,
}: SubscriptionOverflowSettingsProps) {
  const { t } = useTranslation();
  const [helpOpen, setHelpOpen] = useState(false);
  const selectedSourceId = settings.subscriptionOverflowSourceId;
  const eligibleSources = modelSources.filter(isSubscriptionOverflowEligibleSource);
  const selectedSource = modelSources.find((source) => source.id === selectedSourceId);
  // A designated source that lost Responses support (or was never eligible)
  // stays visible but unselectable, like a blocked single-account choice.
  const ineligibleSelectedSource =
    selectedSource !== undefined && !isSubscriptionOverflowEligibleSource(selectedSource) ? selectedSource : null;
  // While the source list is loading or failed to load nothing is known about
  // which sources exist: no deleted marker and no empty-state prompt.
  const modelSourcesUnavailable = modelSourcesLoading || modelSourcesError;
  const unresolvedSelectedSourceId =
    selectedSourceId !== null && selectedSource === undefined ? selectedSourceId : null;
  // A dangling id (source deleted out of band) means "off" server-side; show
  // it so the operator can see what the row still names and clear it.
  const deletedSelectedSourceId = modelSourcesUnavailable ? null : unresolvedSelectedSourceId;
  const pinsExpireBy = settings.subscriptionOverflowPinsExpireBy;
  const draining = selectedSourceId === null && isSubscriptionOverflowDraining(pinsExpireBy);
  const label = t("settings.routing.subscriptionOverflow.label");

  const save = (sourceId: string | null) => {
    void onSave(buildSettingsUpdateRequest(settings, { subscriptionOverflowSourceId: sourceId }));
  };

  return (
    <div className="space-y-3 p-3" data-testid="subscription-overflow-settings">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{t("settings.routing.subscriptionOverflow.description")}</p>
        </div>
        <Select
          value={selectedSourceId ?? SUBSCRIPTION_OVERFLOW_OFF_VALUE}
          onValueChange={(value) => save(value === SUBSCRIPTION_OVERFLOW_OFF_VALUE ? null : value)}
        >
          <SelectTrigger
            aria-label={label}
            className="h-8 w-full text-xs sm:w-64"
            disabled={busy || modelSourcesLoading}
          >
            <SelectValue
              placeholder={
                modelSourcesLoading
                  ? t("settings.routing.subscriptionOverflow.loading")
                  : t("settings.routing.subscriptionOverflow.placeholder")
              }
            />
          </SelectTrigger>
          <SelectContent align="end">
            <SelectItem value={SUBSCRIPTION_OVERFLOW_OFF_VALUE}>
              {t("settings.routing.subscriptionOverflow.off")}
            </SelectItem>
            {ineligibleSelectedSource ? (
              <SelectItem value={ineligibleSelectedSource.id} disabled>
                {t("settings.routing.subscriptionOverflow.ineligibleSource", { name: ineligibleSelectedSource.name })}
              </SelectItem>
            ) : null}
            {deletedSelectedSourceId ? (
              <SelectItem value={deletedSelectedSourceId} disabled>
                {t("settings.routing.subscriptionOverflow.deletedSource", { id: deletedSelectedSourceId })}
              </SelectItem>
            ) : null}
            {modelSourcesError && unresolvedSelectedSourceId ? (
              <SelectItem value={unresolvedSelectedSourceId} disabled>
                {unresolvedSelectedSourceId}
              </SelectItem>
            ) : null}
            {eligibleSources.map((source) => (
              <SelectItem key={source.id} value={source.id}>
                {source.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {modelSourcesError ? (
        <p role="alert" className="text-xs text-destructive">
          {t("settings.routing.subscriptionOverflow.loadFailed")}
        </p>
      ) : null}
      {!modelSourcesUnavailable && eligibleSources.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("settings.routing.subscriptionOverflow.empty")}</p>
      ) : null}
      {draining ? (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-xs text-foreground">
          {t("settings.routing.subscriptionOverflow.drainingUntil", { date: formatDateTimeInline(pinsExpireBy) })}
        </p>
      ) : null}
      {selectedSourceId !== null ? <SubscriptionOverflowPreflightPanel sourceId={selectedSourceId} /> : null}
      <Collapsible open={helpOpen} onOpenChange={setHelpOpen}>
        <CollapsibleTrigger
          type="button"
          className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", helpOpen && "rotate-180")} />
          {t("settings.routing.subscriptionOverflow.help.toggle")}
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 space-y-2 text-xs text-muted-foreground">
          {HELP_KEYS.map((key) => (
            <p key={key}>{t(`settings.routing.subscriptionOverflow.help.${key}`)}</p>
          ))}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function SubscriptionOverflowPreflightPanel({ sourceId }: { sourceId: string }) {
  const { t } = useTranslation();
  const { preflightQuery } = useSubscriptionOverflowPreflight(sourceId);

  if (preflightQuery.isPending) {
    return <p className="text-xs text-muted-foreground">{t("settings.routing.subscriptionOverflow.preflight.loading")}</p>;
  }
  if (preflightQuery.error || !preflightQuery.data) {
    return (
      <p role="alert" className="text-xs text-destructive">
        {t("settings.routing.subscriptionOverflow.preflight.failed")}
      </p>
    );
  }
  return <SubscriptionOverflowPreflightReport preflight={preflightQuery.data} />;
}

function SubscriptionOverflowPreflightReport({ preflight }: { preflight: SubscriptionOverflowPreflight }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 rounded-md border border-border/40 p-2 text-xs" data-testid="subscription-overflow-preflight">
      <div className="flex flex-wrap items-center gap-1.5">
        <p className="font-medium">
          {t("settings.routing.subscriptionOverflow.preflight.title")} · {preflight.sourceName}
        </p>
        {preflight.eligible ? (
          <Badge variant="secondary">{t("settings.routing.subscriptionOverflow.preflight.ready")}</Badge>
        ) : null}
        {!preflight.sourceEnabled ? (
          <Badge variant="outline">{t("settings.routing.subscriptionOverflow.preflight.sourceDisabled")}</Badge>
        ) : null}
        {preflight.blockers.map((blocker) => (
          <Badge key={blocker} variant="destructive">
            {t(`settings.routing.subscriptionOverflow.preflight.blockers.${blocker}`)}
          </Badge>
        ))}
      </div>
      <p className="text-muted-foreground">
        {preflight.missingModels.length > 0
          ? t("settings.routing.subscriptionOverflow.preflight.missingModels", {
              models: preflight.missingModels.join(", "),
            })
          : t("settings.routing.subscriptionOverflow.preflight.noMissingModels")}
      </p>
      {preflight.servedModels.length > 0 ? (
        <ul className="space-y-1">
          {preflight.servedModels.map((model) => (
            <li key={model.slug} className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono">{model.slug}</span>
              {!model.enabled ? (
                <Badge variant="outline">{t("settings.routing.subscriptionOverflow.preflight.modelDisabled")}</Badge>
              ) : null}
              {model.warnings.map((warning) => (
                <Badge key={warning} variant="secondary" className="font-normal">
                  {preflightWarningLabel(t, warning, model)}
                </Badge>
              ))}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-muted-foreground">
        {t("settings.routing.subscriptionOverflow.preflight.scopedKeys", { count: preflight.scopedApiKeyCount })}
      </p>
      <p className="text-muted-foreground">
        {t("settings.routing.subscriptionOverflow.preflight.pins", {
          live: preflight.livePinCount,
          tombstones: preflight.tombstoneCount,
        })}
      </p>
    </div>
  );
}

function preflightWarningLabel(
  t: (key: string, options?: Record<string, unknown>) => string,
  warning: string,
  model: SubscriptionOverflowPreflightModel,
): string {
  switch (warning) {
    case "undeclared_tool_types":
      return t("settings.routing.subscriptionOverflow.preflight.undeclaredTools", {
        types: model.undeclaredToolTypes.join(", "),
      });
    case "no_vision":
      return t("settings.routing.subscriptionOverflow.preflight.noVision");
    case "no_streaming":
      return t("settings.routing.subscriptionOverflow.preflight.noStreaming");
    case "unpriced":
      return t("settings.routing.subscriptionOverflow.preflight.unpriced");
    case "context_window_smaller":
      return t("settings.routing.subscriptionOverflow.preflight.contextWindow", {
        source: model.contextWindowMismatch?.source ?? "?",
        registry: model.contextWindowMismatch?.registry ?? "?",
      });
    case "context_window_missing":
      return t("settings.routing.subscriptionOverflow.preflight.contextWindowMissing", {
        registry: model.contextWindowMismatch?.registry ?? "?",
      });
    case "responses_lite_excluded":
      return t("settings.routing.subscriptionOverflow.preflight.neverOverflows");
    case "not_in_registry":
      return t("settings.routing.subscriptionOverflow.preflight.notInRegistry");
    default:
      return warning;
  }
}
