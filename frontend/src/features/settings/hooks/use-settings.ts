import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSettings, updateSettings } from "@/features/settings/api";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";

export function useSettings() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: getSettings,
  });

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: SettingsUpdateRequest) => updateSettings(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
    },
  });

  return {
    settingsQuery,
    updateSettingsMutation,
  };
}
