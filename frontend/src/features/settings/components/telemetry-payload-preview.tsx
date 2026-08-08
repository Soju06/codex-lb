import type { TelemetrySnapshotEnvelope } from "@/features/settings/schemas";

export type TelemetryPayloadPreviewProps = {
  preview: TelemetrySnapshotEnvelope;
};

export function TelemetryPayloadPreview({ preview }: TelemetryPayloadPreviewProps) {
  return (
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg border bg-muted/20 p-3 text-xs">
      {`${JSON.stringify(preview, null, 2)}\n`}
    </pre>
  );
}
