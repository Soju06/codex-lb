import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { TelemetryPayloadPreview } from "@/features/settings/components/telemetry-payload-preview";
import { useTelemetryConsent } from "@/features/settings/hooks/use-settings";

// Same published page the backend startup notice points operators to
// (TELEMETRY_FIELDS_DOCUMENTATION in app/modules/telemetry/scheduler.py).
const TELEMETRY_DOCS_URL = "https://soju06.github.io/codex-lb/telemetry/";

export function TelemetryConsentDialog() {
  const { t } = useTranslation();
  const canWrite = useAuthStore((state) => state.canWrite);
  const [dismissed, setDismissed] = useState(false);
  // Read-only guests can never act on the dialog, so skip the preview
  // aggregation request entirely instead of fetching and discarding it.
  const { telemetryConsentQuery, updateTelemetryConsentMutation } = useTelemetryConsent({ enabled: canWrite });

  const consent = telemetryConsentQuery.data;
  // The dialog exists to show the exact payload before the first send, so it
  // is skipped when the backend attached no preview envelope.
  const preview = consent?.preview ?? null;
  const open =
    canWrite &&
    !dismissed &&
    consent !== undefined &&
    consent.state === "undecided" &&
    consent.source !== "env" &&
    preview !== null;

  if (!open) {
    return null;
  }

  const busy = updateTelemetryConsentMutation.isPending;
  // Dismissing without a decision (ESC, backdrop, close button) persists
  // nothing; the dialog may reappear on the next dashboard entry.
  const decide = (enabled: boolean) => {
    updateTelemetryConsentMutation.mutate({ enabled }, { onSuccess: () => setDismissed(true) });
  };

  return (
    <Dialog open onOpenChange={(nextOpen) => setDismissed(!nextOpen)}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("settings.telemetry.consentDialog.title")}</DialogTitle>
          <DialogDescription>{t("settings.telemetry.consentDialog.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {t("settings.telemetry.consentDialog.categories")}
          </p>
          <p className="text-sm text-muted-foreground">{t("settings.telemetry.optOutNotice")}</p>
          <p className="text-sm font-medium">{t("settings.telemetry.consentDialog.payloadLabel")}</p>
          <TelemetryPayloadPreview preview={preview} />
          <p className="text-sm">
            <a
              href={TELEMETRY_DOCS_URL}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline underline-offset-2"
            >
              {t("settings.telemetry.consentDialog.docsLink")}
            </a>
          </p>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={() => decide(false)}>
            {t("settings.telemetry.consentDialog.disable")}
          </Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => decide(true)}>
            {t("settings.telemetry.consentDialog.keepEnabled")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
