import { useTranslation } from "react-i18next";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ConversationTimeframe } from "@/features/dashboard/schemas";

const CONVERSATION_TIMEFRAME_VALUES = ["1d", "7d", "30d"] as const;

function isConversationTimeframe(value: string): value is ConversationTimeframe {
  return (CONVERSATION_TIMEFRAME_VALUES as readonly string[]).includes(value);
}

export type ConversationTimeframeSelectProps = {
  value: ConversationTimeframe;
  onChange: (value: ConversationTimeframe) => void;
};

export function ConversationTimeframeSelect({
  value,
  onChange,
}: ConversationTimeframeSelectProps) {
  const { t } = useTranslation();

  return (
    <Select value={value} onValueChange={(next) => { if (isConversationTimeframe(next)) onChange(next); }}>
      <SelectTrigger
        size="sm"
        className="w-28"
        aria-label={t("dashboard.filters.conversationTimeframe")}
      >
        <SelectValue placeholder={t("dashboard.filters.overview")} />
      </SelectTrigger>
      <SelectContent align="end">
        <SelectItem value="1d">1d</SelectItem>
        <SelectItem value="7d">7d</SelectItem>
        <SelectItem value="30d">30d</SelectItem>
      </SelectContent>
    </Select>
  );
}
