import { useMemo, useState } from "react";
import { BookOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SpinnerBlock } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useModelContextWindowOverrides } from "@/features/settings/hooks/use-settings";
import type { ModelContextWindowOverride } from "@/features/settings/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";

export type ModelCatalogueSettingsProps = {
  disabled?: boolean;
};

type Draft = {
  slug: string;
  contextWindow: string;
  /** Slug of the row being edited; the slug input is locked while set. */
  editing: string | null;
};

const EMPTY_DRAFT: Draft = { slug: "", contextWindow: "", editing: null };

function parseContextWindow(value: string): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : null;
}

/**
 * Per-model context window overrides (Settings -> Advanced -> Model catalogue).
 *
 * Each row is a slug and the context window the catalog reports for it,
 * clamped to the upstream `max_context_window`. A dashboard row wins for its
 * slug; a row inherited from `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` is shown
 * read-only with an "Override" action that stores a dashboard row, and removing
 * a dashboard row returns the slug to the environment entry (or to no override).
 */
export function ModelCatalogueSettings({ disabled = false }: ModelCatalogueSettingsProps) {
  const { t } = useTranslation();
  const { overridesQuery, upsertMutation, deleteMutation } = useModelContextWindowOverrides();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const removeDialog = useDialogState<ModelContextWindowOverride>();

  const error = useMemo(
    () =>
      getErrorMessageOrNull(overridesQuery.error) ||
      getErrorMessageOrNull(upsertMutation.error) ||
      getErrorMessageOrNull(deleteMutation.error),
    [overridesQuery.error, upsertMutation.error, deleteMutation.error],
  );

  const overrides = overridesQuery.data?.overrides ?? [];
  const busy = disabled || upsertMutation.isPending || deleteMutation.isPending;
  const slug = (draft.editing ?? draft.slug).trim();
  const contextWindow = parseContextWindow(draft.contextWindow);
  const canSubmit = !busy && slug.length > 0 && !/\s/.test(slug) && contextWindow !== null;

  const handleSubmit = async () => {
    if (!canSubmit || contextWindow === null) {
      return;
    }
    await upsertMutation.mutateAsync({ slug, contextWindow });
    setDraft(EMPTY_DRAFT);
  };

  const startEditing = (override: ModelContextWindowOverride) => {
    setDraft({ slug: override.slug, contextWindow: String(override.contextWindow), editing: override.slug });
  };

  return (
    <section id="model-catalogue" className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <BookOpen className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("settings.modelCatalogue.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("settings.modelCatalogue.description")}</p>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          aria-label={t("settings.modelCatalogue.form.slugLabel")}
          value={draft.editing ?? draft.slug}
          onChange={(event) => setDraft((current) => ({ ...current, slug: event.target.value }))}
          placeholder="gpt-5.4"
          className="h-8 font-mono text-xs"
          disabled={busy || draft.editing !== null}
        />
        <Input
          aria-label={t("settings.modelCatalogue.form.contextWindowLabel")}
          value={draft.contextWindow}
          onChange={(event) => setDraft((current) => ({ ...current, contextWindow: event.target.value }))}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              void handleSubmit();
            }
          }}
          inputMode="numeric"
          placeholder="272000"
          className="h-8 text-xs tabular-nums sm:max-w-[160px]"
          disabled={busy}
        />
        <Button type="button" size="sm" className="h-8 text-xs" onClick={() => void handleSubmit()} disabled={!canSubmit}>
          {draft.editing !== null ? t("common.actions.save") : t("settings.modelCatalogue.actions.add")}
        </Button>
        {draft.editing !== null ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 text-xs"
            onClick={() => setDraft(EMPTY_DRAFT)}
            disabled={busy}
          >
            {t("common.cancel")}
          </Button>
        ) : null}
      </div>
      <p className="text-[11px] text-muted-foreground">{t("settings.modelCatalogue.clampHint")}</p>

      {overridesQuery.isLoading && !overridesQuery.data ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : overrides.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={t("settings.modelCatalogue.empty.title")}
          description={t("settings.modelCatalogue.empty.description")}
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settings.modelCatalogue.table.model")}</TableHead>
                <TableHead>{t("settings.modelCatalogue.table.contextWindow")}</TableHead>
                <TableHead>{t("settings.modelCatalogue.table.source")}</TableHead>
                <TableHead className="w-[200px] text-right">{t("apiKeys.table.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overrides.map((override) => (
                <TableRow key={override.slug}>
                  <TableCell className="font-mono text-xs">{override.slug}</TableCell>
                  <TableCell className="text-xs tabular-nums">{override.contextWindow.toLocaleString()}</TableCell>
                  <TableCell>
                    {override.source === "dashboard" ? (
                      <span className="flex flex-col gap-1">
                        <Badge variant="secondary" className="text-[10px] font-normal">
                          {t("settings.modelCatalogue.source.dashboard")}
                        </Badge>
                        {override.envValue !== null ? (
                          <span className="text-[11px] text-muted-foreground">
                            {t("settings.modelCatalogue.source.environmentValue", {
                              value: override.envValue.toLocaleString(),
                            })}
                          </span>
                        ) : null}
                      </span>
                    ) : (
                      <Badge variant="outline" className="text-[10px] font-normal">
                        {t("settings.inherit.environment", { value: override.contextWindow.toLocaleString() })}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {override.source === "dashboard" ? (
                      <>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => startEditing(override)}
                        >
                          {t("common.actions.edit")}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="text-destructive hover:text-destructive"
                          disabled={busy}
                          onClick={() => removeDialog.show(override)}
                        >
                          {override.envValue !== null ? t("settings.inherit.reset") : t("common.actions.remove")}
                        </Button>
                      </>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => startEditing(override)}
                      >
                        {t("settings.modelCatalogue.actions.override")}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <ConfirmDialog
        open={removeDialog.open}
        title={
          removeDialog.data?.envValue != null
            ? t("settings.modelCatalogue.removeDialog.resetTitle")
            : t("settings.modelCatalogue.removeDialog.title")
        }
        description={
          removeDialog.data?.envValue != null
            ? t("settings.modelCatalogue.removeDialog.resetDescription", {
                slug: removeDialog.data?.slug ?? "",
                value: removeDialog.data?.envValue.toLocaleString() ?? "",
              })
            : t("settings.modelCatalogue.removeDialog.description", { slug: removeDialog.data?.slug ?? "" })
        }
        confirmLabel={
          removeDialog.data?.envValue != null ? t("settings.inherit.reset") : t("common.actions.remove")
        }
        onOpenChange={removeDialog.onOpenChange}
        onConfirm={() => {
          if (!removeDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(removeDialog.data.slug).finally(() => {
            removeDialog.hide();
          });
        }}
      />
    </section>
  );
}
