import { useTranslation } from "react-i18next";
import type { ReadSnapshotMetadata } from "@/schemas/read-snapshot";

export function ReadSnapshotStatus({
  snapshot,
  warming = false,
}: {
  snapshot?: ReadSnapshotMetadata | null;
  warming?: boolean;
}) {
  const { t } = useTranslation();
  if (warming && !snapshot) {
    return <p role="status" className="text-xs text-muted-foreground">{t("common.loading")}</p>;
  }
  if (!snapshot?.generatedAt || (!snapshot.stale && !snapshot.lastError && !snapshot.refreshing)) return null;
  return (
    <p role="status" className="text-xs text-muted-foreground" data-testid="read-snapshot-status">
      {t(snapshot.stale || snapshot.lastError ? "readSnapshot.delayed" : "common.loading")}
    </p>
  );
}
