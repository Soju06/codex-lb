import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type InheritableSettingField,
  useInheritableSetting,
} from "@/features/settings/hooks/use-inheritable-setting";
import type { DashboardSettings, SettingProvenance, SettingsUpdateRequest } from "@/features/settings/schemas";

function formatScalar(value: SettingProvenance["envValue"]): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return typeof value === "boolean" ? (value ? "on" : "off") : String(value);
}

type InheritBadgeProps = {
  settings: DashboardSettings;
  /** Backend setting name, the key of `settings.provenance`. */
  name: string;
  /** Update-request field that is set to null to clear the dashboard value. */
  field: InheritableSettingField;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
  /**
   * Effective inherited value shown as a plain hint when the backend does not
   * report provenance yet (older releases).
   */
  fallbackValue?: number;
};

export function InheritBadge({ settings, name, field, busy, onSave, fallbackValue }: InheritBadgeProps) {
  const { t } = useTranslation();
  const { provenance, resetToInherited } = useInheritableSetting(settings, name, field, onSave);

  if (!provenance) {
    if (fallbackValue === undefined) {
      return null;
    }
    return (
      <span className="block text-[11px] text-muted-foreground">
        {t("settings.routing.accountCapacity.inheritHint", { value: fallbackValue })}
      </span>
    );
  }
  if (provenance.source === "dashboard") {
    return (
      <Button
        type="button"
        variant="ghost"
        size="xs"
        className="h-6 px-1.5 text-[11px] text-muted-foreground"
        disabled={busy}
        onClick={() => void resetToInherited()}
      >
        {t("settings.inherit.reset")}
      </Button>
    );
  }
  return (
    <Badge variant={provenance.source === "env" ? "outline" : "secondary"} className="text-[10px] font-normal">
      {provenance.source === "env"
        ? t("settings.inherit.environment", { value: formatScalar(provenance.envValue) })
        : t("settings.inherit.default", { value: formatScalar(provenance.default) })}
    </Badge>
  );
}
