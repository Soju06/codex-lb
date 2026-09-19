import { Ellipsis, KeyRound, Pencil, Trash2, Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TeamGateChip } from "@/features/team/components/team-gate-chip";
import { formatUsageAgainstCap } from "@/features/team/usage-format";
import type { TeamMember } from "@/features/team/schemas";

export type TeamMemberTableProps = {
  members: TeamMember[];
  busy: boolean;
  onEdit: (member: TeamMember) => void;
  onIssueKey: (member: TeamMember) => void;
  onShowOnboarding: (member: TeamMember) => void;
  onDelete: (member: TeamMember) => void;
};

export function TeamMemberTable({
  members,
  busy,
  onEdit,
  onIssueKey,
  onShowOnboarding,
  onDelete,
}: TeamMemberTableProps) {
  if (members.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No team members yet"
        description="Add a member, then issue them a key to track and cap their usage."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table className="table-fixed">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[18%] min-w-[11rem] pl-4 text-xs font-medium text-muted-foreground">
              Name
            </TableHead>
            <TableHead className="w-[9%] min-w-[6rem] text-xs font-medium text-muted-foreground">
              Status
            </TableHead>
            <TableHead className="w-[17%] min-w-[11rem] text-xs font-medium text-muted-foreground">
              Today
            </TableHead>
            <TableHead className="w-[17%] min-w-[11rem] text-xs font-medium text-muted-foreground">
              This week
            </TableHead>
            <TableHead className="w-[17%] min-w-[11rem] text-xs font-medium text-muted-foreground">
              This month
            </TableHead>
            <TableHead className="w-[9%] min-w-[6rem] text-xs font-medium text-muted-foreground">
              Gate
            </TableHead>
            <TableHead className="w-[7%] min-w-[4.5rem] text-xs font-medium text-muted-foreground">
              Keys
            </TableHead>
            <TableHead className="w-[6%] min-w-[4.5rem] pr-4 text-right text-xs font-medium text-muted-foreground">
              Actions
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((member) => (
            <TableRow key={member.id} data-testid={`team-member-row-${member.id}`}>
              <TableCell className="truncate pl-4 font-medium">
                {member.name}
                {member.email ? (
                  <span className="block truncate text-xs font-normal text-muted-foreground">
                    {member.email}
                  </span>
                ) : null}
              </TableCell>
              <TableCell>
                <Badge variant={member.status === "active" ? "secondary" : "outline"}>
                  {member.status === "active" ? "Active" : "Suspended"}
                </Badge>
              </TableCell>
              <TableCell className="text-xs tabular-nums whitespace-normal">
                {formatUsageAgainstCap(member, "day")}
              </TableCell>
              <TableCell className="text-xs tabular-nums whitespace-normal">
                {formatUsageAgainstCap(member, "week")}
              </TableCell>
              <TableCell className="text-xs tabular-nums whitespace-normal">
                {formatUsageAgainstCap(member, "month")}
              </TableCell>
              <TableCell>
                <TeamGateChip gate={member.gate} />
              </TableCell>
              <TableCell className="text-xs tabular-nums">{member.keys.length}</TableCell>
              <TableCell className="pr-4 text-right">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="icon-sm" variant="ghost" disabled={busy}>
                      <Ellipsis className="size-4" />
                      <span className="sr-only">Actions for {member.name}</span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => onEdit(member)}>
                      <Pencil className="size-4" />
                      Edit
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onIssueKey(member)}>
                      <KeyRound className="size-4" />
                      Issue key
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onShowOnboarding(member)}>
                      Onboarding
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={() => onDelete(member)}>
                      <Trash2 className="size-4" />
                      Remove
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
