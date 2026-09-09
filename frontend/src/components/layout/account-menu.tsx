import { KeyRound, LogOut, ShieldCheck, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordChangeDialog } from "@/features/settings/components/password-change-dialog";
import { cn } from "@/lib/utils";

// Deep link to the TOTP card on the Settings page (`<section id="totp">`).
const TOTP_SETTINGS_PATH = "/settings#totp";

type AccountMenuItem = {
  key: string;
  label: string;
  icon: LucideIcon;
  onSelect: () => void;
  destructive?: boolean;
};

function useAccountMenuItems(onOpenPasswordDialog: () => void): AccountMenuItem[] {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Same predicate the Settings page uses to mount the TOTP card.
  const canManageTotp = useAuthStore(
    (state) => state.canWrite && state.passwordManagementEnabled && state.passwordSessionActive,
  );
  const logout = useAuthStore((state) => state.logout);
  const logoutEverywhere = useAuthStore((state) => state.logoutEverywhere);

  const items: AccountMenuItem[] = [
    { key: "password", label: t("nav.account.myPassword"), icon: KeyRound, onSelect: onOpenPasswordDialog },
  ];
  if (canManageTotp) {
    items.push({
      key: "two-factor",
      label: t("nav.account.myTwoFactor"),
      icon: ShieldCheck,
      onSelect: () => navigate(TOTP_SETTINGS_PATH),
    });
  }
  items.push(
    {
      key: "logout-everywhere",
      label: t("nav.account.logoutEverywhere"),
      icon: LogOut,
      onSelect: () => void logoutEverywhere(),
      destructive: true,
    },
    { key: "logout", label: t("common.logout"), icon: LogOut, onSelect: () => void logout(), destructive: true },
  );
  return items;
}

function initialsOf(username: string): string {
  return username.slice(0, 2).toUpperCase();
}

/** Team-tier header control: avatar chip `username · role` with the account menu. */
export function AccountMenu() {
  const user = useAuthStore((state) => state.user);
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const items = useAccountMenuItems(() => setPasswordDialogOpen(true));

  if (!user) {
    return null;
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="press-scale hidden h-8 gap-2 rounded-lg pr-2.5 pl-1 text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
          >
            <span
              aria-hidden="true"
              className="grid h-6 w-6 place-items-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary"
            >
              {initialsOf(user.username)}
            </span>
            <span className="max-w-[12rem] truncate">
              {user.username}
              {" · "}
              <span className="text-muted-foreground/70">{user.role.name}</span>
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-52">
          <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">
            {user.displayName ?? user.username}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {items.map((item) => (
            <DropdownMenuItem
              key={item.key}
              onSelect={item.onSelect}
              className={cn("cursor-pointer gap-2", item.destructive && "text-destructive focus:text-destructive")}
            >
              <item.icon className="h-3.5 w-3.5" aria-hidden="true" />
              {item.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <PasswordChangeDialog open={passwordDialogOpen} onOpenChange={setPasswordDialogOpen} />
    </>
  );
}
