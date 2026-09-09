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
import type { TelemetryPreview } from "@/features/settings/schemas";

// Same published page the backend startup notice points operators to
// (TELEMETRY_FIELDS_DOCUMENTATION in app/modules/telemetry/scheduler.py).
const TELEMETRY_DOCS_URL = "https://soju06.github.io/codex-lb/telemetry/";

type ShownNotice = {
  preview: TelemetryPreview;
  // A decided installation is only informed: its persisted decision stays as
  // recorded and the informational variant never writes consent back.
  undecided: boolean;
};

export function TelemetryConsentDialog() {
  const { t } = useTranslation();
  const canWrite = useAuthStore((state) => state.canWrite);
  const [shown, setShown] = useState<ShownNotice | null>(null);
  const [dismissed, setDismissed] = useState(false);
  // Read-only guests can never act on the dialog, so skip the preview
  // aggregation request entirely instead of fetching and discarding it.
  const { telemetryConsentQuery, updateTelemetryConsentMutation } = useTelemetryConsent({ enabled: canWrite });

  const consent = telemetryConsentQuery.data;
  // The backend attaches the preview bodies only while a dialog is due: on
  // first entry while undecided, or once more when the transmitted schema grew
  // and this installation has not acknowledged the current notice version.
  // Building that preview acknowledges the notice server-side, so any later
  // refetch (reconnect, a second observer on /settings) returns null. The first
  // preview is latched here so only the operator closes the dialog.
  if (shown === null && consent !== undefined && consent.source !== "env" && consent.preview !== null) {
    setShown({ preview: consent.preview, undecided: consent.state === "undecided" });
  }

  if (!canWrite || dismissed || shown === null) {
    return null;
  }

  const { preview, undecided } = shown;
  const busy = updateTelemetryConsentMutation.isPending;
  // Dismissing without a decision (ESC, backdrop, close button) persists
  // nothing; the undecided dialog may reappear on the next dashboard entry.
  const decide = (enabled: boolean) => {
    updateTelemetryConsentMutation.mutate({ enabled }, { onSuccess: () => setDismissed(true) });
  };

  return (
    <Dialog open onOpenChange={(nextOpen) => setDismissed(!nextOpen)}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {undecided
              ? t("settings.telemetry.consentDialog.title")
              : t("settings.telemetry.noticeDialog.title")}
          </DialogTitle>
          <DialogDescription>
            {undecided
              ? t("settings.telemetry.consentDialog.description")
              : t("settings.telemetry.noticeDialog.description")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {t("settings.telemetry.consentDialog.categories")}
          </p>
          {undecided ? (
            <p className="text-sm text-muted-foreground">{t("settings.telemetry.optOutNotice")}</p>
          ) : null}
          <p className="text-sm text-muted-foreground">{t("settings.telemetry.retentionNotice")}</p>
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
          {undecided ? (
            <>
              <Button type="button" variant="outline" disabled={busy} onClick={() => decide(false)}>
                {t("settings.telemetry.consentDialog.disable")}
              </Button>
              <Button type="button" variant="outline" disabled={busy} onClick={() => decide(true)}>
                {t("settings.telemetry.consentDialog.keepEnabled")}
              </Button>
            </>
          ) : (
            <Button type="button" variant="outline" onClick={() => setDismissed(true)}>
              {t("settings.telemetry.noticeDialog.acknowledge")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
