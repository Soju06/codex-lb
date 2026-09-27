import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { AccountListItem, type AccountListItemProps } from "@/features/accounts/components/account-list-item";
import { AccountSubscription } from "@/features/accounts/components/account-subscription";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import {
  formatCompactNumber,
  formatDateTimeInline,
} from "@/utils/formatters";

export function AccountGridCard(props: AccountListItemProps) {
  const { account, onSelect } = props;
  const { t } = useTranslation();
  const dateFormat = useDateDisplayFormatStore((s) => s.dateDisplayFormat);
  return (
    <article
      data-testid="account-grid-card"
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3"
    >
      <AccountListItem {...props} showQuota={false} />
      <AccountSubscription account={account} />
      <AccountUsagePanel
        account={account}
        resetCredits={{ availableCount: account.availableResetCredits ?? 0 }}
      />
      <AccountTokenInfo account={account} />
      <dl className="space-y-1 px-1 text-xs text-muted-foreground">
        <div className="flex flex-wrap justify-between gap-2">
          <dt>{t("accounts.grid.credits")}</dt>
          <dd>
            {account.creditsUnlimited
              ? t("accounts.grid.unlimited")
              : account.creditsBalance != null
                ? formatCompactNumber(account.creditsBalance)
                : "—"}
          </dd>
        </div>
        <div className="flex flex-wrap justify-between gap-2">
          <dt>{t("accounts.grid.refreshed")}</dt>
          <dd>{formatDateTimeInline(account.lastRefreshAt, dateFormat)}</dd>
        </div>
        {account.workspaceLabel || account.workspaceId ? (
          <div className="flex flex-wrap justify-between gap-2">
            <dt>{t("accounts.grid.workspace")}</dt>
            <dd className="min-w-0 break-all">
              {account.workspaceLabel || account.workspaceId}
            </dd>
          </div>
        ) : null}
      </dl>
      {account.deactivationReason ? (
        <p className="break-words text-xs text-amber-600 dark:text-amber-400">
          {account.deactivationReason}
        </p>
      ) : null}
      <Button
        className="mt-auto w-full"
        variant="outline"
        onClick={() => onSelect(account.accountId)}
      >
        {t("accounts.grid.details")}
        <ArrowUpRight className="size-3.5" aria-hidden="true" />
      </Button>
    </article>
  );
}
