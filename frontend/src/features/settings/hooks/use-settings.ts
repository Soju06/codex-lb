import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getSettings, updateSettings } from "@/features/settings/api";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";

export function useSettings(enabled = true) {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: getSettings,
    enabled,
  });

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: SettingsUpdateRequest) => updateSettings(payload),
    onSuccess: () => {
      toast.success("Settings saved");
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to save settings");
    },
  });

  return {
    settingsQuery,
    updateSettingsMutation,
  };
}
