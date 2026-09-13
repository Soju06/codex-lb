import { Lock } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { isPresetRole, orderRolesForPicker, type PickerRole } from "@/features/organisation/rules";
import { useTranslation } from "react-i18next";

/**
 * The options every role picker shows: the built-in presets first, in the
 * order people meet them, each marked with a lock because they cannot be
 * edited. Custom roles follow only once the install has made one — until then
 * the concept is not mentioned at all.
 *
 * The count comes from the session summary; a caller that may not see it
 * (no `users:manage`) offers the presets only, which is what it can assign.
 */
function RoleOption({ role }: { role: PickerRole }) {
  return (
    <SelectItem value={role.id}>
      <span className="flex items-center gap-1.5">
        {isPresetRole(role) ? <Lock className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}
        {role.name}
      </span>
    </SelectItem>
  );
}

export function RoleSelectItems({ roles }: { roles: readonly PickerRole[] }) {
  const { t } = useTranslation();
  const customRoles = useAuthStore((state) => state.accessSummary?.customRoles ?? 0);
  const ordered = orderRolesForPicker(roles, { customRoles });
  const presets = ordered.filter(isPresetRole);
  const custom = ordered.filter((role) => !isPresetRole(role));
  return (
    <>
      {presets.map((role) => (
        <RoleOption key={role.id} role={role} />
      ))}
      {custom.length > 0 ? (
        <SelectGroup>
          <SelectLabel>{t("access.roles.customHeading")}</SelectLabel>
          {custom.map((role) => (
            <RoleOption key={role.id} role={role} />
          ))}
        </SelectGroup>
      ) : null}
    </>
  );
}

export type RolePickerProps = {
  value: string;
  onValueChange: (roleId: string) => void;
  roles: readonly PickerRole[];
  disabled?: boolean;
  ariaLabel: string;
  placeholder?: string;
  className?: string;
};

/** A complete role `Select` for callers that do not need their own trigger. */
export function RolePicker({
  value,
  onValueChange,
  roles,
  disabled = false,
  ariaLabel,
  placeholder,
  className,
}: RolePickerProps) {
  return (
    <Select value={value} onValueChange={onValueChange} disabled={disabled || roles.length === 0}>
      <SelectTrigger aria-label={ariaLabel} className={className}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <RoleSelectItems roles={roles} />
      </SelectContent>
    </Select>
  );
}
