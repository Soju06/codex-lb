import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";

export type MultiSelectOption = {
  value: string;
  label: string;
};

export type MultiSelectFilterProps = {
  label: string;
  values: string[];
  options: MultiSelectOption[];
  onChange: (values: string[]) => void;
};

export function MultiSelectFilter({ label, values, options, onChange }: MultiSelectFilterProps) {
  const toggleValue = (value: string) => {
    if (values.includes(value)) {
      onChange(values.filter((entry) => entry !== value));
      return;
    }
    onChange([...values, value]);
  };

  const summary =
    values.length === 0
      ? label
      : values.length === 1
        ? options.find((option) => option.value === values[0])?.label || `${label} (1)`
        : `${label} (${values.length})`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="justify-between">
          {summary}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 min-w-56 overflow-y-auto">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {options.length === 0 ? (
          <p className="px-2 py-1 text-xs text-muted-foreground">No options</p>
        ) : (
          options.map((option) => (
            <DropdownMenuCheckboxItem
              key={option.value}
              checked={values.includes(option.value)}
              onCheckedChange={() => toggleValue(option.value)}
            >
              {option.label}
            </DropdownMenuCheckboxItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
