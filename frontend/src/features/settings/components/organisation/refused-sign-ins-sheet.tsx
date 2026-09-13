import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/empty-state";
import { ShieldX } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { SpinnerBlock } from "@/components/ui/spinner";
import type { AuditEntry } from "@/features/organisation/api";
import { REFUSED_WINDOW_DAYS } from "@/features/organisation/rules";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { formatTimeLong } from "@/utils/formatters";

function detailText(entry: AuditEntry, key: string): string | null {
  const value = entry.details?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

export type RefusedSignInsSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entries: readonly AuditEntry[];
  loading: boolean;
};

/**
 * The audit log filtered to the refusals this card counts: `login_failed`
 * with `reason=unknown_identity`, over the same seven-day window. It is a
 * read of `/api/audit-logs`, so an account without `audit:read` never gets
 * here — the counter line that opens it is not rendered for them.
 */
export function RefusedSignInsSheet({ open, onOpenChange, entries, loading }: RefusedSignInsSheetProps) {
  const { t } = useTranslation();
  const dateDisplayFormat = useDateDisplayFormatStore((state) => state.dateDisplayFormat);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t("organisation.refused.title")}</SheetTitle>
          <SheetDescription>
            {t("organisation.refused.description", { days: REFUSED_WINDOW_DAYS })}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-2 px-4 pb-4">
          {loading ? (
            <SpinnerBlock />
          ) : entries.length === 0 ? (
            <EmptyState icon={ShieldX} title={t("organisation.refused.empty")} />
          ) : (
            entries.map((entry) => {
              const when = formatTimeLong(entry.timestamp, dateDisplayFormat);
              const subject = detailText(entry, "subject") ?? t("organisation.refused.unknownSubject");
              const email = detailText(entry, "email");
              return (
                <div key={entry.id} data-testid="refused-sign-in-row" className="rounded-lg border p-3">
                  <p className="font-mono text-xs font-medium">{subject}</p>
                  {email ? <p className="text-xs text-muted-foreground">{email}</p> : null}
                  <p className="text-[11px] text-muted-foreground">
                    {when.date} {when.time}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
