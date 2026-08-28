import { Activity } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { TelemetryPayloadPreview } from "@/features/settings/components/telemetry-payload-preview";
import { useTelemetryConsent, useTelemetryPreview } from "@/features/settings/hooks/use-settings";

export type TelemetrySettingsProps = {
  disabled: boolean;
};

export function TelemetrySettings({ disabled }: TelemetrySettingsProps) {
  const { t } = useTranslation();
  const [previewOpen, setPreviewOpen] = useState(false);
  const { telemetryConsentQuery, updateTelemetryConsentMutation } = useTelemetryConsent();
  // Building the snapshot is expensive, so the preview is fetched only once
  // the operator opens the dialog.
  const { telemetryPreviewQuery } = useTelemetryPreview(previewOpen);

  const consent = telemetryConsentQuery.data;
  const envControlled = consent?.source === "env";
  const busy = disabled || updateTelemetryConsentMutation.isPending || !consent;
  const previewEnvelope = telemetryPreviewQuery.data?.preview ?? null;

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.telemetry.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.telemetry.description")}</p>
            </div>
          </div>
          <Switch
            aria-label={t("settings.telemetry.toggleAria")}
            checked={consent?.active ?? false}
            disabled={busy || envControlled}
            onCheckedChange={(checked) => updateTelemetryConsentMutation.mutate({ enabled: checked })}
          />
        </div>

        <p className="text-xs text-muted-foreground">{t("settings.telemetry.optOutNotice")}</p>

        {envControlled ? (
          <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-foreground">
            {t("settings.telemetry.envNotice")}
          </div>
        ) : null}

        <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">{t("settings.telemetry.collectedData.label")}</p>
            <p className="text-xs text-muted-foreground">
              {t("settings.telemetry.collectedData.description")}
            </p>
          </div>
          <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
            <DialogTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={!consent}
              >
                {t("settings.telemetry.collectedData.view")}
              </Button>
            </DialogTrigger>
            {previewOpen ? (
              <DialogContent className="sm:max-w-2xl">
                <DialogHeader>
                  <DialogTitle>{t("settings.telemetry.previewDialog.title")}</DialogTitle>
                  <DialogDescription>
                    {t("settings.telemetry.previewDialog.description")}
                  </DialogDescription>
                </DialogHeader>
                {previewEnvelope ? (
                  <TelemetryPayloadPreview preview={previewEnvelope} />
                ) : telemetryPreviewQuery.error ? (
                  <AlertMessage variant="error">{telemetryPreviewQuery.error.message}</AlertMessage>
                ) : (
                  <Skeleton className="h-64 w-full rounded-lg" />
                )}
                <DialogFooter showCloseButton />
              </DialogContent>
            ) : null}
          </Dialog>
        </div>
      </div>
    </section>
  );
}
