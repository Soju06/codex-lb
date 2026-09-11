import { useState } from "react";
import { Archive } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ConversationArchiveSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

/** Backend setting name: dashboard column, env alias and provenance key. */
const SETTING_NAME = "conversation_archive_enabled";

/**
 * M5 conversation archive: the opt-in full prompt/response recorder.
 *
 * Enabling turns the proxy into a recorder of every upstream request and
 * response body, readable by any dashboard admin from request-log details, so
 * the switch never PUTs `true` on its own: turning it on opens a confirmation
 * dialog and only the dialog's confirm action saves. Turning it off saves
 * immediately. The archive directory is environment-only (T1) because each
 * replica writes its own local shard; it is shown read-only for orientation.
 */
export function ConversationArchiveSettings({ settings, busy, onSave }: ConversationArchiveSettingsProps) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const enabled = settings.conversationArchiveEnabled;
  const provenance = settings.provenance?.[SETTING_NAME];
  // "Reset to inherited" would silently start recording when the deprecated
  // env alias is on and the dashboard value is off, so that path is blocked;
  // the operator turns the switch on (and confirms) instead.
  const resetWouldEnable = !enabled && provenance?.source === "dashboard" && provenance.envValue === true;

  const store = (value: boolean) =>
    void onSave(buildSettingsUpdateRequest(settings, { conversationArchiveEnabled: value }));

  const handleCheckedChange = (checked: boolean) => {
    if (checked) {
      setConfirmOpen(true);
      return;
    }
    store(false);
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Archive className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.conversationArchive.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.conversationArchive.description")}</p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t("settings.conversationArchive.toggle.label")}</p>
            <p className="text-xs text-muted-foreground">{t("settings.conversationArchive.toggle.description")}</p>
            <InheritBadge
              settings={settings}
              name={SETTING_NAME}
              field="conversationArchiveEnabled"
              busy={busy}
              onSave={onSave}
              resetBlockedReason={resetWouldEnable ? t("settings.conversationArchive.resetBlocked") : undefined}
            />
          </div>
          <Switch
            aria-label={t("settings.conversationArchive.toggle.ariaLabel")}
            checked={enabled}
            disabled={busy}
            onCheckedChange={handleCheckedChange}
          />
        </div>

        {enabled ? (
          <div
            role="status"
            className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs font-medium text-foreground"
          >
            {t("settings.conversationArchive.recordingNotice")}
          </div>
        ) : null}

        <div className="space-y-1 rounded-lg border p-3">
          <p className="text-sm font-medium">{t("settings.conversationArchive.directory.label")}</p>
          <code className="block break-all rounded bg-muted px-2 py-1 font-mono text-xs">
            {settings.conversationArchiveDir ?? t("settings.conversationArchive.directory.unavailable")}
          </code>
          <p className="text-xs text-muted-foreground">{t("settings.conversationArchive.directory.note")}</p>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={t("settings.conversationArchive.confirm.title")}
        description={t("settings.conversationArchive.confirm.description")}
        confirmLabel={t("settings.conversationArchive.confirm.action")}
        onConfirm={() => {
          setConfirmOpen(false);
          store(true);
        }}
        onOpenChange={setConfirmOpen}
      />
    </section>
  );
}
