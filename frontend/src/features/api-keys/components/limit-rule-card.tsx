import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import {
  LIMIT_TYPES,
  LIMIT_WINDOWS,
  type LimitRuleCreate,
  type LimitType,
  type LimitWindowType,
} from "@/features/api-keys/schemas";

const LIMIT_TYPE_LABELS: Record<LimitType, string> = {
  total_tokens: "Total Tokens",
  input_tokens: "Input Tokens",
  output_tokens: "Output Tokens",
  cost_usd: "Cost ($)",
};

const WINDOW_LABELS: Record<LimitWindowType, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
};

const LIMIT_TYPE_SET: ReadonlySet<string> = new Set(LIMIT_TYPES);
const LIMIT_WINDOW_SET: ReadonlySet<string> = new Set(LIMIT_WINDOWS);

function isLimitType(v: string): v is LimitType {
  return LIMIT_TYPE_SET.has(v);
}

function isLimitWindow(v: string): v is LimitWindowType {
  return LIMIT_WINDOW_SET.has(v);
}

export type LimitRuleCardProps = {
  rule: LimitRuleCreate;
  onChange: (rule: LimitRuleCreate) => void;
  onRemove: () => void;
};

export function LimitRuleCard({ rule, onChange, onRemove }: LimitRuleCardProps) {
  const isCost = rule.limitType === "cost_usd";
  const displayValue = isCost && rule.maxValue > 0
    ? String(rule.maxValue / 1_000_000)
    : rule.maxValue > 0
      ? String(rule.maxValue)
      : "";

  const handleValueChange = (raw: string) => {
    if (!raw) {
      onChange({ ...rule, maxValue: 0 });
      return;
    }
    if (isCost) {
      const usd = parseFloat(raw);
      if (!isNaN(usd)) {
        onChange({ ...rule, maxValue: Math.round(usd * 1_000_000) });
      }
    } else {
      const val = parseInt(raw, 10);
      if (!isNaN(val)) {
        onChange({ ...rule, maxValue: val });
      }
    }
  };

  const handleLimitTypeChange = (v: string) => {
    if (isLimitType(v)) {
      onChange({ ...rule, limitType: v });
    }
  };

  const handleWindowChange = (v: string) => {
    if (isLimitWindow(v)) {
      onChange({ ...rule, limitWindow: v });
    }
  };

  const modelFilterArray = rule.modelFilter ? [rule.modelFilter] : [];

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">Limit rule</span>
        <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">Type</label>
          <Select value={rule.limitType} onValueChange={handleLimitTypeChange}>
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LIMIT_TYPES.map((k) => (
                <SelectItem key={k} value={k}>
                  {LIMIT_TYPE_LABELS[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <label className="text-xs text-muted-foreground">Window</label>
          <Select value={rule.limitWindow} onValueChange={handleWindowChange}>
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LIMIT_WINDOWS.map((k) => (
                <SelectItem key={k} value={k}>
                  {WINDOW_LABELS[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div>
        <label className="text-xs text-muted-foreground">
          {isCost ? "Max value (USD)" : "Max value (tokens)"}
        </label>
        <Input
          type="number"
          min={isCost ? 0.01 : 1}
          step={isCost ? 0.01 : 1}
          value={displayValue}
          onChange={(e) => handleValueChange(e.target.value)}
          className="h-8 text-xs"
        />
      </div>

      <div>
        <label className="text-xs text-muted-foreground">Model filter</label>
        <ModelMultiSelect
          value={modelFilterArray}
          onChange={(models) => onChange({ ...rule, modelFilter: models[0] || null })}
          placeholder="All models"
        />
      </div>
    </div>
  );
}
