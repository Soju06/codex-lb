import { useCallback } from "react";

import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingProvenance, SettingsUpdateRequest } from "@/features/settings/schemas";

// Update-request fields that accept an explicit null to return to inheritance.
export type InheritableSettingField = {
  [K in keyof SettingsUpdateRequest]-?: null extends SettingsUpdateRequest[K] ? K : never;
}[keyof SettingsUpdateRequest];

/**
 * Provenance of one inheritable setting (keyed by its backend name) plus a
 * reset action that PUTs an explicit null so the setting returns to inheriting
 * the environment value or code default.
 */
export function useInheritableSetting(
  settings: DashboardSettings,
  name: string,
  field: InheritableSettingField,
  onSave: (payload: SettingsUpdateRequest) => Promise<void>,
): { provenance: SettingProvenance | undefined; resetToInherited: () => Promise<void> } {
  const provenance = settings.provenance?.[name];
  const resetToInherited = useCallback(
    () => onSave(buildSettingsUpdateRequest(settings, { [field]: null })),
    [field, onSave, settings],
  );
  return { provenance, resetToInherited };
}
