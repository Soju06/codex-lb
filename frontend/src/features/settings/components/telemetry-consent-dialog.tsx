import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { cn } from "@/lib/utils";

// Same published page the backend startup notice points operators to
// (TELEMETRY_FIELDS_DOCUMENTATION in app/modules/telemetry/scheduler.py).
const TELEMETRY_DOCS_URL = "https://soju06.github.io/codex-lb/telemetry/";
// Dismissing without deciding is remembered per browser so the dialog is asked
// once rather than on every dashboard entry. It records only that the question
// was shown; consent itself stays `undecided` and is owned by the backend.
export const CONSENT_DIALOG_DISMISSED_STORAGE_KEY = "codex-lb.telemetry-consent-dismissed";

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(CONSENT_DIALOG_DISMISSED_STORAGE_KEY) === "1";
  } catch {
    // Private-mode or blocked storage: fall back to asking again rather than
    // suppressing the dialog on a read failure.
    return false;
  }
}

function persistDismissed(): void {
  try {
    window.localStorage.setItem(CONSENT_DIALOG_DISMISSED_STORAGE_KEY, "1");
  } catch {
    // A browser that refuses the write keeps today's behaviour: the dialog may
    // reappear. Nothing else depends on this key.
  }
}

export function TelemetryConsentDialog() {
  const { t } = useTranslation();
  const canWrite = useAuthStore((state) => state.canWrite);
  const [dismissed, setDismissed] = useState(readDismissed);
  const [payloadOpen, setPayloadOpen] = useState(false);
  // Read-only guests can never act on the dialog, and a browser that already
  // dismissed it will not show it again, so skip the preview aggregation
  // request entirely in both cases instead of fetching and discarding it.
  const { telemetryConsentQuery, updateTelemetryConsentMutation } = useTelemetryConsent({
    enabled: canWrite && !dismissed,
  });

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
  const decide = (enabled: boolean) => {
    updateTelemetryConsentMutation.mutate({ enabled }, { onSuccess: () => setDismissed(true) });
  };
  // Dismissing without a decision (ESC, backdrop, close button) persists no
  // consent decision; it only records that this browser was asked.
  const dismiss = () => {
    persistDismissed();
    setDismissed(true);
  };

  return (
    <Dialog
      open
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          dismiss();
        }
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("settings.telemetry.consentDialog.title")}</DialogTitle>
          <DialogDescription>{t("settings.telemetry.consentDialog.description")}</DialogDescription>
        </DialogHeader>
        <Collapsible open={payloadOpen} onOpenChange={setPayloadOpen}>
          <div className="flex items-center justify-between gap-3">
            <CollapsibleTrigger asChild>
              <Button type="button" variant="ghost" size="sm" className="h-8 gap-1 px-2 text-xs">
                <ChevronRight
                  className={cn("h-3.5 w-3.5 transition-transform", payloadOpen && "rotate-90")}
                  aria-hidden="true"
                />
                {t("settings.telemetry.consentDialog.viewPayload")}
              </Button>
            </CollapsibleTrigger>
            <a
              href={TELEMETRY_DOCS_URL}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground underline underline-offset-2"
            >
              {t("settings.telemetry.consentDialog.docsLink")}
            </a>
          </div>
          <CollapsibleContent className="pt-2">
            <TelemetryPayloadPreview preview={preview} />
          </CollapsibleContent>
        </Collapsible>
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
