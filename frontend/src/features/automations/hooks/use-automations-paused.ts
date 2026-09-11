import { useSettings } from "@/features/settings/hooks/use-settings";

/**
 * Effective automations pause state for the page (undefined while settings load).
 *
 * Reads the same dashboard setting as the header toggle
 * (`automations_scheduler_enabled`). The backend refuses "Run now" while the
 * scheduler is paused, so the page disables that action instead of letting it fail.
 */
export function useAutomationsPaused(): boolean | undefined {
  const { settingsQuery } = useSettings();
  const settings = settingsQuery.data;
  return settings ? !settings.automationsSchedulerEnabled : undefined;
}
