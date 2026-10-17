import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { DashboardRole, PermissionDescriptor } from "@/features/access/api";
import { describeGrants } from "@/features/access/describe-grants";

export type RoleBadgeProps = {
  role: { id: string; name: string };
  roles: readonly DashboardRole[];
  descriptors: readonly PermissionDescriptor[];
};

/** Role name with the role's permission list on hover. */
export function RoleBadge({ role, roles, descriptors }: RoleBadgeProps) {
  const { t } = useTranslation();
  const detail = roles.find((candidate) => candidate.id === role.id);
  const lines = detail ? describeGrants(detail, descriptors, t) : [];
  const badge = (
    <Badge variant="outline" className="cursor-default">
      {role.name}
    </Badge>
  );
  if (lines.length === 0) {
    return badge;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <ul className="list-disc space-y-0.5 pl-4 text-left">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}
