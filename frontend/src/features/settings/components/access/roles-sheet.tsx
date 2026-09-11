import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { describeGrants } from "@/features/access/describe-grants";
import { useDashboardRoles, usePermissionDescriptors } from "@/features/access/hooks";
import { cn } from "@/lib/utils";

// Presets in the order people meet them; Guest last because it is not an account.
const PRESET_ORDER = ["admin", "operator", "member", "viewer", "guest"];

/**
 * Read-only view of the built-in roles. Nothing here edits or clones a role:
 * the custom-role editor is a later phase and gets no stub.
 */
export function RolesSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const rolesQuery = useDashboardRoles(open);
  const permissionsQuery = usePermissionDescriptors(open);
  const descriptors = permissionsQuery.data ?? [];
  const roles = [...(rolesQuery.data ?? [])].sort(
    (a, b) => PRESET_ORDER.indexOf(a.slug) - PRESET_ORDER.indexOf(b.slug),
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t("access.roles.title")}</SheetTitle>
          <SheetDescription>{t("access.roles.description")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-3 px-4 pb-4">
          {roles.map((role) => {
            const comingLater = role.slug === "member";
            const lines = describeGrants(role, descriptors, t);
            return (
              <section
                key={role.id}
                data-testid={`role-card-${role.slug}`}
                className={cn("space-y-2 rounded-lg border p-3", comingLater && "opacity-60")}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-sm font-semibold">{role.name}</h4>
                  {comingLater ? <Badge variant="outline">{t("access.roles.comingLater")}</Badge> : null}
                  {role.slug === "guest" ? (
                    <span className="text-xs text-muted-foreground">{t("access.roles.guestNote")}</span>
                  ) : null}
                </div>
                <p className="text-xs text-muted-foreground">
                  {t(`access.roles.${role.slug}.summary`, { defaultValue: role.description ?? "" })}
                </p>
                <ul className="list-disc space-y-0.5 pl-4 text-xs">
                  {lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </SheetContent>
    </Sheet>
  );
}
