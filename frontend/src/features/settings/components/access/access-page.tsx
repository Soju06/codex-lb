import { Users } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router-dom";

import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import { AccessPeopleTab } from "@/features/settings/components/access/access-people-tab";
import { InviteDialog, IssuedLinkDialog, type IssuedLink } from "@/features/settings/components/access/invite-dialog";

/**
 * `/settings/access`: the People tab on its own page for teams the card gets
 * too small for. Not a navigation item; reachable by URL on every tier for
 * signed-in `users:manage` holders on a standard-auth install, everyone else
 * lands back on Settings (same guard as the card's People tab).
 */
export function AccessPage() {
  const { t } = useTranslation();
  const canManageUsers = usePermission("users:manage");
  const user = useAuthStore((state) => state.user);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [issued, setIssued] = useState<IssuedLink | null>(null);

  if (!canManageUsers || user === null) {
    return <Navigate to="/settings" replace />;
  }
  return (
    <div className="animate-fade-in-up space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Users className="h-5 w-5 text-primary" aria-hidden="true" />
          {t("access.page.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("access.page.subtitle")}{" "}
          <Link to="/settings" className="text-primary underline-offset-4 hover:underline">
            {t("access.page.backToSettings")}
          </Link>
        </p>
      </div>
      <AccessPeopleTab fullPage onInvite={() => setInviteOpen(true)} onIssued={setIssued} />
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} onIssued={setIssued} />
      <IssuedLinkDialog
        issued={issued}
        onClose={() => {
          setIssued(null);
          void useAuthStore.getState().refreshSession().catch(() => undefined);
        }}
      />
    </div>
  );
}
