import { useState } from "react";
import { CompanyModelHealth } from "./company-model-health";
import { CompanySourceStatus } from "@/features/model-sources/components/company-source-status";
import { discoverTraeModels, getCodebaseModelPresets } from "@/features/model-sources/api";
import { Database, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ModelSourceCreateDialog } from "@/features/model-sources/components/model-source-create-dialog";
import { ModelSourceEditDialog } from "@/features/model-sources/components/model-source-edit-dialog";
import { useModelSources } from "@/features/model-sources/hooks/use-model-sources";
import type {
  ModelSource,
  ModelSourceCreateRequest,
  ModelSourceUpdateRequest,
} from "@/features/model-sources/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";

function modelPriceLabel(source: ModelSource): string | null {
  const priced = source.models.find(
    (model) => model.inputPer1M !== null || model.outputPer1M !== null,
  );
  if (!priced) return null;
  const input = priced.inputPer1M ?? 0;
  const output = priced.outputPer1M ?? 0;
  return `$${input}/$${output} per 1M`;
}

export type ModelSourcesSettingsProps = {
  disabled?: boolean;
};

function protocolBadges(source: ModelSource) {
  return [
    source.supportsChatCompletions ? "chat" : null,
    source.supportsResponses ? "responses" : null,
    source.supportsAudioTranscriptions ? "audio" : null,
    source.supportsEmbeddings ? "embeddings" : null,
  ].filter((value): value is string => value !== null);
}

export function ModelSourcesSettings({ disabled = false }: ModelSourcesSettingsProps) {
  const { t } = useTranslation();
  const [discovering, setDiscovering] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const {
    modelSourcesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  } = useModelSources();
  const createDialog = useDialogState();
  const editDialog = useDialogState<ModelSource>();
  const deleteDialog = useDialogState<ModelSource>();
  const sources = modelSourcesQuery.data?.sources ?? [];
  const busy =
    disabled ||
    discovering ||
    modelSourcesQuery.isFetching ||
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending;
  const error =
    discoveryError ||
    getErrorMessageOrNull(modelSourcesQuery.error) ||
    getErrorMessageOrNull(createMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error);

  const createSource = async (payload: ModelSourceCreateRequest) => {
    await createMutation.mutateAsync(payload);
  };

  const updateSource = async (sourceId: string, payload: ModelSourceUpdateRequest) => {
    await updateMutation.mutateAsync({ sourceId, payload });
  };

  const addTrae = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const models = await discoverTraeModels();
      await createMutation.mutateAsync({kind: "trae", name: "TRAE", baseUrl: "https://copilot-cn.bytedance.net/api/ide/v2", supportsChatCompletions: false, supportsResponses: true, timeoutSeconds: 900, maxConcurrency: 1, models});
    } catch (error) {
      setDiscoveryError(getErrorMessageOrNull(error));
    } finally {
      setDiscovering(false);
    }
  };

  const addCodebase = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const models = await getCodebaseModelPresets();
      await createMutation.mutateAsync({kind: "codebase_llm", name: "Codebase / Coco", baseUrl: "https://codebase-api.byted.org/v2/api/2022-06-01/LLMProxy/Model", supportsChatCompletions: false, supportsResponses: true, timeoutSeconds: 300, maxConcurrency: 1, models});
    } catch (error) {
      setDiscoveryError(getErrorMessageOrNull(error));
    } finally {
      setDiscovering(false);
    }
  };

  return (
    <section id="model-sources" className="scroll-mt-20 min-w-0 space-y-4 rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Database className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
	            <h3 className="text-sm font-semibold">{t("modelSources.title")}</h3>
	            <p className="text-xs text-muted-foreground">{t("modelSources.description")}</p>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" size="sm" variant="outline"
          disabled={busy || sources.some((source) => source.kind === "codebase_llm")}
          onClick={() => void addCodebase()}>
          {t("modelSources.company.addCodebase")}
        </Button>
        <Button type="button" size="sm" variant="outline"
          disabled={busy || sources.some((source) => source.kind === "trae")}
          onClick={() => void addTrae()}>
          {t("modelSources.company.addTrae")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || sources.some((source) => source.kind === "llmbox")}
          onClick={() => void createMutation.mutateAsync({
            kind: "llmbox",
            name: "LLMBox",
            baseUrl: "https://llmbox.bytedance.net/v1",
            supportsChatCompletions: false,
            supportsResponses: true,
            maxConcurrency: 1,
            models: [{ model: "deepseek-v4-flash-0731", displayName: "LLMBox DeepSeek V4 Flash",
              supportsStreaming: true, supportsTools: true, isEnabled: false }],
          })}
        >
          {t("modelSources.company.add")}
        </Button>
        <Button
          type="button"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={busy}
          onClick={() => createDialog.show()}
        >
          <Plus className="h-3.5 w-3.5" />
	          {t("modelSources.actions.addSource")}
        </Button>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      <div className="space-y-2">
        {sources.length > 0 ? (
          sources.map((source) => (
            <div key={source.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{source.name}</span>
                    <Badge variant={source.isEnabled ? "default" : "secondary"}>
	                      {source.isEnabled ? t("common.states.enabled") : t("common.states.disabled")}
                    </Badge>
                    {protocolBadges(source).map((protocol) => (
                      <Badge key={protocol} variant="secondary">
                        {protocol}
                      </Badge>
                    ))}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{source.baseUrl}</div>
                  {source.companyStatus ? (
                    <div className="space-y-1 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                      <p>{t("modelSources.company.quotaUnknown")}</p>
                      <CompanySourceStatus key={`${source.id}-${source.localTokenBudget}`} source={source} disabled={busy} onSave={(localTokenBudget) => updateMutation.mutateAsync({ sourceId: source.id, payload: { localTokenBudget } })} />
                      <p>{t(source.companyStatus.credentialCache === "present"
                        ? "modelSources.company.cachePresent" : "modelSources.company.cacheUnavailable")}</p>
                      <p>{t(source.kind === "trae" ? "modelSources.company.traeDiscovery" : source.kind === "codebase_llm" ? "modelSources.company.codebaseNative" : "modelSources.company.dynamic")}</p>
                      {source.companyStatus.observedUsage ? (
                        <p>{t("modelSources.company.observed", {
                          requests: source.companyStatus.observedUsage.requests,
                          input: source.companyStatus.observedUsage.inputTokens ?? "—",
                          output: source.companyStatus.observedUsage.outputTokens ?? "—",
                          missing: source.companyStatus.observedUsage.requestsWithoutUsage,
                        })}</p>
                      ) : null}
                    </div>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-1 pt-1">
                    {!source.companyStatus && source.models.map((model) => (
                      <Badge key={model.id} variant={model.isEnabled ? "outline" : "secondary"}>
                        {model.model}
                      </Badge>
                    ))}
                    {modelPriceLabel(source) ? (
                      <Badge variant="secondary">{modelPriceLabel(source)}</Badge>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Switch
	                    aria-label={t("modelSources.actions.toggleAria", { name: source.name })}
                    checked={source.isEnabled}
                    disabled={busy}
                    onCheckedChange={(checked) =>
                      void updateMutation.mutateAsync({
                        sourceId: source.id,
                        payload: { isEnabled: checked },
                      })
                    }
                  />
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => editDialog.show(source)}
                  >
                    <Pencil className="size-4" />
	                    <span className="sr-only">{t("modelSources.actions.editAria", { name: source.name })}</span>
                  </Button>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => deleteDialog.show(source)}
                  >
                    <Trash2 className="size-4" />
	                    <span className="sr-only">{t("modelSources.actions.deleteAria", { name: source.name })}</span>
                  </Button>
                </div>
              </div>
              <CompanyModelHealth source={source} />
            </div>
          ))
        ) : (
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
	            {t("modelSources.empty")}
          </div>
        )}
      </div>

      <ModelSourceCreateDialog
        open={createDialog.open}
        busy={createMutation.isPending}
        onOpenChange={createDialog.onOpenChange}
        onSubmit={createSource}
      />

      <ModelSourceEditDialog
        open={editDialog.open}
        busy={updateMutation.isPending}
        source={editDialog.data}
        onOpenChange={editDialog.onOpenChange}
        onSubmit={updateSource}
      />

      <ConfirmDialog
        open={deleteDialog.open}
	        title={t("modelSources.deleteDialog.title")}
	        description={t("modelSources.deleteDialog.description")}
	        confirmLabel={t("common.actions.delete")}
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) return;
          void deleteMutation.mutateAsync(deleteDialog.data.id).finally(() => deleteDialog.hide());
        }}
      />
    </section>
  );
}
