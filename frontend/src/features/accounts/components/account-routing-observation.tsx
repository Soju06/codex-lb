import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { getAccountRoutingObservation } from "@/features/accounts/api";

export function AccountRoutingObservation({ accountId }: { accountId: string }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["accounts", "routing-observation", accountId],
    queryFn: () => getAccountRoutingObservation(accountId),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });
  const state = query.data;
  return (
    <div className="space-y-2 rounded-lg border p-3 text-sm">
      <p className="font-medium">{t("accounts.routing.local")}</p>
      <p className="text-xs text-muted-foreground">{t("accounts.routing.scope")}</p>
      {query.error ? <AlertMessage variant="error">{query.error.message}</AlertMessage> : null}
      {state ? <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        {[
          [t("accounts.routing.health"), ["healthy", "draining", "probing"][state.healthTier] ?? state.healthTier],
          [t("accounts.routing.errors"), state.recentErrors],
          [t("accounts.routing.streams"), `${state.inflightStreams} / ${state.inflightResponseCreates}`],
          [t("accounts.routing.lastSelected"), state.lastSelectedAt],
          [t("accounts.routing.cooldown"), state.cooldownUntil],
          [t("accounts.routing.overload"), state.overloadBackoffUntil],
          [t("accounts.routing.burst"), state.burstBackoffUntil],
        ].map(([label, value]) => <div key={label} className="min-w-0">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="break-words font-mono">{value ?? "—"}</dd>
        </div>)}
      </dl> : null}
    </div>
  );
}
