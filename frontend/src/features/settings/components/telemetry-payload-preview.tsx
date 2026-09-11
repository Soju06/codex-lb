import { useTranslation } from "react-i18next";

import type { TelemetryPreview } from "@/features/settings/schemas";

export type TelemetryPayloadPreviewProps = {
  preview: TelemetryPreview;
};

// Renders both transmitted bodies verbatim, each under its own label: the
// heartbeat envelope and the most recent completed UTC day. One scroll region
// keeps the dialog height constant while the sticky labels keep it clear which
// body is on screen.
export function TelemetryPayloadPreview({ preview }: TelemetryPayloadPreviewProps) {
  const { t } = useTranslation();
  const bodies = [
    { key: "heartbeat", label: t("settings.telemetry.preview.heartbeat"), body: preview.heartbeat },
    { key: "day", label: t("settings.telemetry.preview.completedDay"), body: preview.day },
  ];

  return (
    <div className="max-h-72 overflow-auto rounded-lg border bg-muted/20 text-xs">
      {bodies.map(({ key, label, body }) => (
        <section key={key} aria-label={label}>
          <p className="sticky top-0 border-b bg-muted px-3 py-1.5 font-medium">{label}</p>
          <pre className="whitespace-pre-wrap break-all p-3">{`${JSON.stringify(body, null, 2)}\n`}</pre>
        </section>
      ))}
    </div>
  );
}
