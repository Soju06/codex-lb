import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import {
  ACCESS_CARD_ID,
  ACCESS_HASH,
  ACCESS_PEOPLE_HASH,
  accessTabFromHash,
  type AccessTab,
} from "@/features/settings/advanced-settings-deeplink";
import { AccessMySignInTab, type AccessMySignInTabProps } from "@/features/settings/components/access/access-my-sign-in-tab";
import { AccessPeopleTab } from "@/features/settings/components/access/access-people-tab";
import { AccessSoloBody } from "@/features/settings/components/access/access-solo-body";
import { InviteDialog, IssuedLinkDialog, type IssuedLink } from "@/features/settings/components/access/invite-dialog";

/**
 * The one Settings card for "who can open this dashboard". Its body follows the
 * store's derived tier: an individual install sees a single line plus today's
 * four controls; a team sees a People tab next to those controls. The hash
 * `#access-people` selects People, `#access` the person's own controls.
 *
 * The invite flow lives here, outside the tier-dependent subtree: creating the
 * first invite flips the tier and would otherwise unmount the dialog that has
 * to show the one-time link.
 */
export function AccessCard(props: AccessMySignInTabProps) {
  const { t } = useTranslation();
  const { hash, key: locationKey } = useLocation();
  const tier = useAuthStore((state) => state.tier);
  const user = useAuthStore((state) => state.user);
  const canManageUsers = usePermission("users:manage");
  const hashTab = accessTabFromHash(hash);
  // The hash wins on every navigation; a click overrides it only for the
  // current history entry (remembered together with the location key).
  const [choice, setChoice] = useState<{ key: string; tab: AccessTab } | null>(null);
  const tab: AccessTab = choice?.key === locationKey ? choice.tab : (hashTab ?? "people");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [issued, setIssued] = useState<IssuedLink | null>(null);
  // People needs a team, the permission, and an account the backend can
  // attribute the changes to (`409 admin_account_required` otherwise): the
  // implicit local admin and the disabled-auth principal get their own
  // sign-in controls only. A reverse-proxy account is an account.
  const showPeople = tier !== "individual" && canManageUsers && user !== null;

  useEffect(() => {
    if (hashTab === null) {
      return;
    }
    // `#totp` targets the TOTP section itself once the tab has mounted it.
    const targetId = hash === ACCESS_HASH || hash === ACCESS_PEOPLE_HASH ? ACCESS_CARD_ID : hash.slice(1);
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(targetId)?.scrollIntoView({ block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [hash, hashTab, locationKey]);

  const openInvite = () => setInviteOpen(true);
  const closeIssued = () => {
    setIssued(null);
    // The link has been acknowledged; now let the tier catch up with the new row.
    void useAuthStore.getState().refreshSession().catch(() => undefined);
  };

  return (
    <section id={ACCESS_CARD_ID} className="scroll-mt-20 rounded-xl border bg-card p-5">
      <div className="space-y-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Users className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("access.card.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("access.card.description")}</p>
          </div>
        </div>

        {showPeople ? (
          <Tabs value={tab} onValueChange={(next) => setChoice({ key: locationKey, tab: next as AccessTab })}>
            <TabsList aria-label={t("access.card.title")}>
              <TabsTrigger value="people">{t("access.tabs.people")}</TabsTrigger>
              <TabsTrigger value="my-sign-in">{t("access.tabs.mySignIn")}</TabsTrigger>
            </TabsList>
            <TabsContent value="people">
              <AccessPeopleTab
                onOpenMySignIn={() => setChoice({ key: locationKey, tab: "my-sign-in" })}
                onInvite={openInvite}
                onIssued={setIssued}
              />
            </TabsContent>
            <TabsContent value="my-sign-in">
              <AccessMySignInTab {...props} />
            </TabsContent>
          </Tabs>
        ) : tier === "individual" ? (
          <AccessSoloBody {...props} onInvite={openInvite} />
        ) : (
          <AccessMySignInTab {...props} />
        )}
      </div>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} onIssued={setIssued} />
      <IssuedLinkDialog issued={issued} onClose={closeIssued} />
    </section>
  );
}
