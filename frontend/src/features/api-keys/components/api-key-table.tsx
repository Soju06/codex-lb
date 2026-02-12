import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ApiKey } from "@/features/api-keys/schemas";
import { formatCompactNumber, formatTimeLong } from "@/utils/formatters";

function formatExpiry(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const parsed = formatTimeLong(value);
  return `${parsed.date} ${parsed.time}`;
}

export type ApiKeyTableProps = {
  keys: ApiKey[];
  busy: boolean;
  onEdit: (apiKey: ApiKey) => void;
  onDelete: (apiKey: ApiKey) => void;
  onRegenerate: (apiKey: ApiKey) => void;
};

export function ApiKeyTable({ keys, busy, onEdit, onDelete, onRegenerate }: ApiKeyTableProps) {
  if (keys.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
        No API keys created yet.
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Prefix</TableHead>
          <TableHead>Models</TableHead>
          <TableHead className="text-right">Usage</TableHead>
          <TableHead>Expiry</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((apiKey) => {
          const models = apiKey.allowedModels?.join(", ") || "All";
          const limitText = apiKey.weeklyTokenLimit
            ? `${formatCompactNumber(apiKey.weeklyTokensUsed)} / ${formatCompactNumber(apiKey.weeklyTokenLimit)}`
            : formatCompactNumber(apiKey.weeklyTokensUsed);

          return (
            <TableRow key={apiKey.id}>
              <TableCell className="font-medium">{apiKey.name}</TableCell>
              <TableCell className="font-mono text-xs">{apiKey.keyPrefix}</TableCell>
              <TableCell className="max-w-[14rem] truncate">{models}</TableCell>
              <TableCell className="text-right tabular-nums">{limitText}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{formatExpiry(apiKey.expiresAt)}</TableCell>
              <TableCell>
                <Badge className={apiKey.isActive ? "bg-emerald-500 text-white" : "bg-zinc-500 text-white"}>
                  {apiKey.isActive ? "Active" : "Disabled"}
                </Badge>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  <Button type="button" size="sm" variant="outline" onClick={() => onEdit(apiKey)} disabled={busy}>
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onRegenerate(apiKey)}
                    disabled={busy}
                  >
                    Regenerate
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    onClick={() => onDelete(apiKey)}
                    disabled={busy}
                  >
                    Delete
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
