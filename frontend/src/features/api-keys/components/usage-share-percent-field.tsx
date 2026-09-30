import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";

type UsageSharePercentFieldProps = {
  id: string;
  value: string;
  onChange: (value: string) => void;
};

export function UsageSharePercentField({
  id,
  value,
  onChange,
}: UsageSharePercentFieldProps) {
  const { t } = useTranslation();
  const descriptionId = `${id}-description`;

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {t("apiKeys.form.usageSharePercent")}
      </label>
      <Input
        id={id}
        type="number"
        min={1}
        max={100}
        step={1}
        inputMode="numeric"
        value={value}
        aria-describedby={descriptionId}
        onChange={(event) => onChange(event.target.value)}
      />
      <p id={descriptionId} className="text-xs text-muted-foreground">
        {t("apiKeys.form.usageShareHelp")}
      </p>
    </div>
  );
}
