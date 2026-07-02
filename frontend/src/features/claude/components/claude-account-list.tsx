import { Bot, Loader2, MoreVertical } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ClaudeAccount } from "@/features/claude/schemas";
import { formatDateTimeInline } from "@/utils/formatters";

const UUID_TRUNCATE_PREFIX = 8;
const UUID_TRUNCATE_SUFFIX = 4;

function truncateUuid(uuid: string): string {
  if (uuid.length <= UUID_TRUNCATE_PREFIX + UUID_TRUNCATE_SUFFIX + 1) {
    return uuid;
  }
  return `${uuid.slice(0, UUID_TRUNCATE_PREFIX)}…${uuid.slice(-UUID_TRUNCATE_SUFFIX)}`;
}

export type ClaudeAccountListProps = {
  accounts: ClaudeAccount[];
  busy?: boolean;
  onDisable: (accountId: string) => void;
  onEnable: (accountId: string) => void;
};

export function ClaudeAccountList({
  accounts,
  busy = false,
  onDisable,
  onEnable,
}: ClaudeAccountListProps) {
  const { t } = useTranslation();

  if (accounts.length === 0) {
    return (
      <EmptyState
        icon={Bot}
        title={t("claude.emptyState.title")}
        description={t("claude.emptyState.description")}
      />
    );
  }

  return (
    <div className="rounded-md border" data-testid="claude-account-list">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("claude.table.email")}</TableHead>
            <TableHead>{t("claude.table.uuid")}</TableHead>
            <TableHead>{t("claude.table.status")}</TableHead>
            <TableHead>{t("claude.table.expiresAt")}</TableHead>
            <TableHead>{t("claude.table.lastUsedAt")}</TableHead>
            <TableHead>{t("claude.table.createdAt")}</TableHead>
            <TableHead className="w-12 text-right">{t("claude.table.actions")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {accounts.map((account) => {
            const statusKey = (account.status ?? "").toLowerCase();
            const statusLabel = t(`claude.statusLabels.${statusKey || "unknown"}`);
            return (
              <TableRow key={account.id}>
                <TableCell className="text-sm">{account.userEmail ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {truncateUuid(account.claudeAccountUuid)}
                </TableCell>
                <TableCell className="text-sm">{statusLabel}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTimeInline(account.claudeAccessTokenExpiresAt ?? null)}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTimeInline(account.lastUsedAt ?? null)}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTimeInline(account.createdAt)}
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        disabled={busy}
                        aria-label={t("claude.table.actions")}
                      >
                        {busy ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : (
                          <MoreVertical className="h-3.5 w-3.5" aria-hidden />
                        )}
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {account.isActive ? (
                        <DropdownMenuItem onSelect={() => onDisable(account.id)}>
                          {t("claude.actions.disable")}
                        </DropdownMenuItem>
                      ) : (
                        <DropdownMenuItem onSelect={() => onEnable(account.id)}>
                          {t("claude.actions.enable")}
                        </DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}