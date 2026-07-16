import { useTranslation } from "react-i18next";

import type { AccountSummary } from "@/features/accounts/schemas";
import {
  formatAccessTokenLabel,
  formatIdTokenLabel,
  formatRefreshTokenLabel,
} from "@/utils/formatters";

export type AccountTokenInfoProps = {
  account: AccountSummary;
};

export function AccountTokenInfo({ account }: AccountTokenInfoProps) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("accounts.tokenInfo.title")}</h3>
      <dl className="space-y-2 text-xs">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <dt className="text-muted-foreground">{t("accounts.tokenInfo.access")}</dt>
          <dd className="min-w-0 break-words text-right font-medium">{formatAccessTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex min-w-0 items-center justify-between gap-2">
          <dt className="text-muted-foreground">{t("accounts.tokenInfo.refresh")}</dt>
          <dd className="min-w-0 break-words text-right font-medium">{formatRefreshTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex min-w-0 items-center justify-between gap-2">
          <dt className="text-muted-foreground">{t("accounts.tokenInfo.idToken")}</dt>
          <dd className="min-w-0 break-words text-right font-medium">{formatIdTokenLabel(account.auth)}</dd>
        </div>
      </dl>
    </div>
  );
}
