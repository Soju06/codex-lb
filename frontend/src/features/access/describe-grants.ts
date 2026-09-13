import type { TFunction } from "i18next";

import type { DashboardRole, PermissionDescriptor } from "@/features/access/api";

/** One plain-words line per grant, from the permission vocabulary the server describes. */
export function describeGrants(
  role: DashboardRole,
  descriptors: readonly PermissionDescriptor[],
  t: TFunction,
): string[] {
  return role.grants.map((grant) => {
    const description =
      descriptors.find((descriptor) => descriptor.permission === grant.permission)?.description ?? grant.permission;
    return grant.scope === "own" ? t("access.roles.ownScope", { description }) : description;
  });
}
