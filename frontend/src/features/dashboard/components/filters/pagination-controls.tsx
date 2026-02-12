import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

export type PaginationControlsProps = {
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
};

export function PaginationControls({
  total,
  limit,
  offset,
  hasMore,
  onLimitChange,
  onOffsetChange,
}: PaginationControlsProps) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">Rows</span>
      <Select value={String(limit)} onValueChange={(value) => onLimitChange(Number(value))}>
        <SelectTrigger size="sm" className="w-20">
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="end">
          {PAGE_SIZE_OPTIONS.map((size) => (
            <SelectItem key={size} value={String(size)}>
              {size}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <span className="text-muted-foreground">{currentPage}/{totalPages}</span>

      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={offset <= 0}
        onClick={() => onOffsetChange(Math.max(0, offset - limit))}
      >
        Prev
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!hasMore}
        onClick={() => onOffsetChange(offset + limit)}
      >
        Next
      </Button>
    </div>
  );
}
