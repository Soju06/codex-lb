import { useReducer } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AccountMultiSelect } from "@/features/api-keys/components/account-multi-select";
import { ExpiryPicker } from "@/features/api-keys/components/expiry-picker";
import { LimitRulesEditor } from "@/features/api-keys/components/limit-rules-editor";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import { UsageSectionsMultiSelect } from "@/features/api-keys/components/usage-sections-multi-select";
import { ModelSourceMultiSelect } from "@/features/model-sources/components/model-source-multi-select";
import type {
  ApiKeyCreateRequest,
  LimitRuleCreate,
  ReasoningEffortType,
  ServiceTierType,
  TrafficClass,
  TransportPolicyOverride,
} from "@/features/api-keys/schemas";

const TRANSPORT_POLICY_FOLLOW_GLOBAL = "follow_global";
const TRANSPORT_POLICY_LABELS = {
  smart: "apiKeys.transport.smart",
  always_http: "apiKeys.transport.alwaysHttp",
  always_websocket: "apiKeys.transport.alwaysWebsocket",
} as const;

type FormValues = {
  name: string;
};

export type ApiKeyCreateDialogProps = {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ApiKeyCreateRequest) => Promise<void>;
};

type ApiKeyCreateFormProps = {
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: ApiKeyCreateRequest) => Promise<void>;
};

type ApiKeyCreateDraft = {
  selectedModels: string[];
  selectedAccountIds: string[];
  selectedSourceIds: string[];
  usageSections: string;
  limitRules: LimitRuleCreate[];
  expiresAt: Date | null;
  enforcedModel: string;
  enforcedReasoningEffort: string;
  enforcedServiceTier: string;
  trafficClass: TrafficClass;
  transportPolicyOverride: TransportPolicyOverride | null;
  applyToCodexModel: boolean;
};

const initialApiKeyCreateDraft: ApiKeyCreateDraft = {
  selectedModels: [],
  selectedAccountIds: [],
  selectedSourceIds: [],
  usageSections: "upstream_limits,account_pool_usage",
  limitRules: [],
  expiresAt: null,
  enforcedModel: "",
  enforcedReasoningEffort: "none",
  enforcedServiceTier: "none",
  trafficClass: "foreground",
  transportPolicyOverride: null,
  applyToCodexModel: false,
};

function apiKeyCreateDraftReducer(
  state: ApiKeyCreateDraft,
  patch: Partial<ApiKeyCreateDraft>,
): ApiKeyCreateDraft {
  return { ...state, ...patch };
}

function ApiKeyCreateForm({ busy, onClose, onSubmit }: ApiKeyCreateFormProps) {
  const { t } = useTranslation();
  const formSchema = z.object({
    name: z.string().min(1, t("apiKeys.validation.nameRequired")),
  });
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "" },
  });

  const [draft, updateDraft] = useReducer(apiKeyCreateDraftReducer, initialApiKeyCreateDraft);

  const handleSubmit = async (values: FormValues) => {
    const validLimits = draft.limitRules.filter((rule) => rule.maxValue > 0);
    const payload: ApiKeyCreateRequest = {
      name: values.name,
      allowedModels: draft.selectedModels.length > 0 ? draft.selectedModels : undefined,
      applyToCodexModel: draft.applyToCodexModel,
      ...(draft.selectedAccountIds.length > 0 ? { assignedAccountIds: draft.selectedAccountIds } : {}),
      ...(draft.selectedSourceIds.length > 0 ? { assignedSourceIds: draft.selectedSourceIds } : {}),
      usageSections: draft.usageSections,
      enforcedModel: draft.enforcedModel.trim() ? draft.enforcedModel.trim() : null,
      enforcedReasoningEffort:
        draft.enforcedReasoningEffort === "none"
          ? null
          : draft.enforcedReasoningEffort as ReasoningEffortType,
      enforcedServiceTier: draft.enforcedServiceTier === "none" ? null : draft.enforcedServiceTier as ServiceTierType,
      trafficClass: draft.trafficClass,
      transportPolicyOverride: draft.transportPolicyOverride,
      expiresAt: draft.expiresAt?.toISOString(),
      limits: validLimits.length > 0 ? validLimits : undefined,
    };

    try {
      await onSubmit(payload);
    } catch {
      return;
    }

    onClose();
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)}>
        <div className="grid gap-x-6 sm:grid-cols-2">
          <div className="max-h-[55vh] space-y-3 overflow-y-auto overscroll-contain pl-1 pr-2">
            <h4 className="sticky top-0 bg-background pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("apiKeys.form.general")}</h4>

            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("apiKeys.form.name")}</FormLabel>
                  <FormControl>
                    <Input {...field} autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="space-y-1">
              <p className="text-sm font-medium">{t("apiKeys.form.allowedModels")}</p>
              <ModelMultiSelect value={draft.selectedModels} onChange={(selectedModels) => updateDraft({ selectedModels })} />
            </div>

            <div className="flex items-center gap-2 rounded-md border p-2 text-sm">
              <Checkbox
                id="create-api-key-apply-to-codex-model"
                checked={draft.applyToCodexModel}
                onCheckedChange={(checked) => updateDraft({ applyToCodexModel: checked === true })}
              />
              <label htmlFor="create-api-key-apply-to-codex-model" className="cursor-pointer">
                {t("apiKeys.form.applyToCodexModel")}
              </label>
            </div>

            <div className="space-y-1">
              <p className="text-sm font-medium">{t("apiKeys.form.assignedAccounts")}</p>
              <AccountMultiSelect value={draft.selectedAccountIds} onChange={(selectedAccountIds) => updateDraft({ selectedAccountIds })} />
            </div>

            <div className="space-y-1">
              <p className="text-sm font-medium">{t("apiKeys.form.assignedModelSources")}</p>
              <ModelSourceMultiSelect
                value={draft.selectedSourceIds}
                onChange={(selectedSourceIds) => updateDraft({ selectedSourceIds })}
              />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">{t("apiKeys.form.usageSections")}</label>
              <UsageSectionsMultiSelect value={draft.usageSections} onChange={(usageSections) => updateDraft({ usageSections })} />
            </div>

            <div className="space-y-1">
              <label htmlFor="create-api-key-enforced-model" className="text-sm font-medium">{t("apiKeys.form.enforcedModel")}</label>
              <Input
                id="create-api-key-enforced-model"
                value={draft.enforcedModel}
                onChange={(e) => updateDraft({ enforcedModel: e.target.value })}
                placeholder="e.g. gpt-5.3-codex"
                autoComplete="off"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="create-api-key-enforced-reasoning" className="text-sm font-medium">{t("apiKeys.form.enforcedReasoning")}</label>
              <Select value={draft.enforcedReasoningEffort} onValueChange={(enforcedReasoningEffort) => updateDraft({ enforcedReasoningEffort })}>
                <SelectTrigger id="create-api-key-enforced-reasoning">
                  <SelectValue placeholder={t("common.options.none")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t("common.options.none")}</SelectItem>
                  <SelectItem value="minimal">{t("common.reasoning.minimal")}</SelectItem>
                  <SelectItem value="low">{t("common.reasoning.low")}</SelectItem>
                  <SelectItem value="medium">{t("common.reasoning.medium")}</SelectItem>
                  <SelectItem value="high">{t("common.reasoning.high")}</SelectItem>
                  <SelectItem value="xhigh">{t("common.reasoning.xhigh")}</SelectItem>
                  <SelectItem value="max">{t("common.reasoning.max")}</SelectItem>
                  <SelectItem value="ultra">{t("common.reasoning.ultra")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <label htmlFor="create-api-key-enforced-service-tier" className="text-sm font-medium">{t("apiKeys.form.enforcedServiceTier")}</label>
              <Select value={draft.enforcedServiceTier} onValueChange={(enforcedServiceTier) => updateDraft({ enforcedServiceTier })}>
                <SelectTrigger id="create-api-key-enforced-service-tier">
                  <SelectValue placeholder={t("common.options.none")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t("common.options.none")}</SelectItem>
                  <SelectItem value="auto">{t("common.serviceTier.auto")}</SelectItem>
                  <SelectItem value="default">{t("common.serviceTier.default")}</SelectItem>
                  <SelectItem value="priority">{t("common.serviceTier.priority")}</SelectItem>
                  <SelectItem value="flex">{t("common.serviceTier.flex")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <FormLabel htmlFor="create-api-key-traffic-class">{t("apiKeys.form.trafficClass")}</FormLabel>
              <Select value={draft.trafficClass} onValueChange={(value) => updateDraft({ trafficClass: value as TrafficClass })}>
                <SelectTrigger id="create-api-key-traffic-class">
                  <SelectValue placeholder={t("common.traffic.foreground")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="foreground">{t("common.traffic.foreground")}</SelectItem>
                  <SelectItem value="opportunistic">{t("common.traffic.opportunistic")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <FormLabel htmlFor="create-api-key-transport-policy">{t("apiKeys.form.httpClientRouting")}</FormLabel>
              <Select
                value={draft.transportPolicyOverride ?? TRANSPORT_POLICY_FOLLOW_GLOBAL}
                onValueChange={(value) =>
                  updateDraft({
                    transportPolicyOverride:
                      value === TRANSPORT_POLICY_FOLLOW_GLOBAL ? null : value as TransportPolicyOverride,
                  })
                }
              >
                <SelectTrigger id="create-api-key-transport-policy">
                  <SelectValue placeholder={t("apiKeys.transport.followGlobal")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={TRANSPORT_POLICY_FOLLOW_GLOBAL}>{t("apiKeys.transport.followGlobal")}</SelectItem>
                  {Object.entries(TRANSPORT_POLICY_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {t(label)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <p className="text-sm font-medium">{t("apiKeys.form.expiry")}</p>
              <ExpiryPicker value={draft.expiresAt} onChange={(expiresAt) => updateDraft({ expiresAt })} />
            </div>
          </div>

          <div className="max-h-[55vh] space-y-3 overflow-y-auto overscroll-contain pl-1 pr-2 max-sm:mt-3 max-sm:border-t max-sm:pt-3">
            <h4 className="sticky top-0 bg-background pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("apiKeys.form.limits")}</h4>
            <LimitRulesEditor rules={draft.limitRules} onChange={(limitRules) => updateDraft({ limitRules })} />
          </div>
        </div>

        <DialogFooter className="mt-4">
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
            {t("common.actions.create")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function ApiKeyCreateDialog({ open, busy, onOpenChange, onSubmit }: ApiKeyCreateDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("apiKeys.createDialog.title")}</DialogTitle>
            <DialogDescription>{t("apiKeys.createDialog.description")}</DialogDescription>
          </DialogHeader>
          <ApiKeyCreateForm busy={busy} onClose={() => onOpenChange(false)} onSubmit={onSubmit} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
