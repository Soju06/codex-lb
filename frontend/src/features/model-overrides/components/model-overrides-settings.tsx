import { ShieldAlert, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useModelOverrides } from "@/features/model-overrides/hooks/use-model-overrides";
import type { ModelOverrideCreateRequest, ModelOverrideMatchType } from "@/features/model-overrides/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

const MATCH_LABELS: Record<ModelOverrideMatchType, string> = {
  ip: "IP",
  app: "App",
  api_key: "API key",
};

export function ModelOverridesSettings() {
  const { overridesQuery, createMutation, updateMutation, deleteMutation } = useModelOverrides();
  const [draft, setDraft] = useState<ModelOverrideCreateRequest>({
    matchType: "app",
    matchValue: "",
    forcedModel: "gpt-5.3-codex",
    forcedReasoningEffort: null,
    enabled: true,
    note: null,
  });

  const busy = overridesQuery.isFetching || createMutation.isPending || updateMutation.isPending || deleteMutation.isPending;
  const items = overridesQuery.data ?? [];

  const errorMessage = useMemo(
    () =>
      getErrorMessageOrNull(overridesQuery.error) ||
      getErrorMessageOrNull(createMutation.error) ||
      getErrorMessageOrNull(updateMutation.error) ||
      getErrorMessageOrNull(deleteMutation.error),
    [createMutation.error, deleteMutation.error, overridesQuery.error, updateMutation.error],
  );

  const handleCreate = async () => {
    if (!draft.matchValue.trim() || !draft.forcedModel.trim()) {
      return;
    }
    await createMutation.mutateAsync({
      ...draft,
      matchValue: draft.matchValue.trim(),
      forcedModel: draft.forcedModel.trim(),
      forcedReasoningEffort: draft.forcedReasoningEffort?.trim() || null,
      note: draft.note?.trim() || null,
    });
    setDraft((prev) => ({
      ...prev,
      matchValue: "",
      note: null,
    }));
  };

  return (
    <section className="space-y-4 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <ShieldAlert className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Model Overrides</h3>
          <p className="text-xs text-muted-foreground">
            Force specific model/effort by API key, app, or IP.
          </p>
        </div>
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      <div className="grid gap-2 rounded-lg border p-3 md:grid-cols-6">
        <Select
          value={draft.matchType}
          onValueChange={(value) => setDraft((prev) => ({ ...prev, matchType: value as ModelOverrideMatchType }))}
        >
          <SelectTrigger className="h-8 text-xs md:col-span-1" disabled={busy}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="start">
            <SelectItem value="app">App</SelectItem>
            <SelectItem value="ip">IP</SelectItem>
            <SelectItem value="api_key">API key</SelectItem>
          </SelectContent>
        </Select>
        <Input
          value={draft.matchValue}
          onChange={(event) => setDraft((prev) => ({ ...prev, matchValue: event.target.value }))}
          className="h-8 text-xs md:col-span-2"
          placeholder="match value (exact)"
          disabled={busy}
        />
        <Input
          value={draft.forcedModel}
          onChange={(event) => setDraft((prev) => ({ ...prev, forcedModel: event.target.value }))}
          className="h-8 text-xs md:col-span-2"
          placeholder="forced model"
          disabled={busy}
        />
        <Button type="button" size="sm" className="h-8 text-xs md:col-span-1" onClick={() => void handleCreate()} disabled={busy}>
          Add rule
        </Button>
        <Input
          value={draft.forcedReasoningEffort ?? ""}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, forcedReasoningEffort: event.target.value || null }))
          }
          className="h-8 text-xs md:col-span-2"
          placeholder="effort override (low|normal|high|xhigh)"
          disabled={busy}
        />
        <Input
          value={draft.note ?? ""}
          onChange={(event) => setDraft((prev) => ({ ...prev, note: event.target.value || null }))}
          className="h-8 text-xs md:col-span-3"
          placeholder="note (optional)"
          disabled={busy}
        />
      </div>

      <div className="rounded-lg border">
        <div className="relative overflow-x-auto">
          <Table className="min-w-[880px]">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Type</TableHead>
                <TableHead>Matcher</TableHead>
                <TableHead>Forced model</TableHead>
                <TableHead>Effort</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const updated = formatTimeLong(item.updatedAt);
                return (
                  <TableRow key={item.id}>
                    <TableCell>{MATCH_LABELS[item.matchType]}</TableCell>
                    <TableCell className="font-mono text-xs">{item.matchValue}</TableCell>
                    <TableCell className="font-mono text-xs">{item.forcedModel}</TableCell>
                    <TableCell className="font-mono text-xs">{item.forcedReasoningEffort ?? "-"}</TableCell>
                    <TableCell>
                      <Switch
                        checked={item.enabled}
                        disabled={busy}
                        onCheckedChange={(checked) =>
                          void updateMutation.mutateAsync({
                            overrideId: item.id,
                            payload: { enabled: checked },
                          })
                        }
                      />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {updated.time} {updated.date}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        disabled={busy}
                        onClick={() => void deleteMutation.mutateAsync(item.id)}
                        title="Delete rule"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
              {items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-6 text-center text-sm text-muted-foreground">
                    No override rules yet.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      </div>
    </section>
  );
}

