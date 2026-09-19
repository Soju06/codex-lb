import { useMemo } from "react";
import { Users } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Button } from "@/components/ui/button";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { TeamMemberDrawer } from "@/features/team/components/team-member-drawer";
import { TeamMemberTable } from "@/features/team/components/team-member-table";
import { TeamOnboardingDialog } from "@/features/team/components/team-onboarding-dialog";
import { useTeamMembers } from "@/features/team/hooks/use-team";
import type {
  TeamMember,
  TeamMemberCreateRequest,
  TeamMemberUpdateRequest,
} from "@/features/team/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";

export function TeamPage() {
  const { membersQuery, createMutation, updateMutation, deleteMutation, issueKeyMutation } =
    useTeamMembers();

  const createDrawer = useDialogState();
  const editDrawer = useDialogState<TeamMember>();
  const deleteDialog = useDialogState<TeamMember>();
  const onboardingDialog = useDialogState<TeamMember>();
  const issuedKeyDialog = useDialogState<string>();

  const members = useMemo(() => membersQuery.data ?? [], [membersQuery.data]);
  const busy =
    membersQuery.isFetching ||
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    issueKeyMutation.isPending;

  const error =
    getErrorMessageOrNull(membersQuery.error) ||
    getErrorMessageOrNull(createMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error) ||
    getErrorMessageOrNull(issueKeyMutation.error);

  const closeDrawer = () => {
    createDrawer.hide();
    editDrawer.hide();
  };

  const handleSubmit = async (payload: TeamMemberCreateRequest | TeamMemberUpdateRequest) => {
    const editing = editDrawer.data;
    if (editing) {
      await updateMutation.mutateAsync({
        memberId: editing.id,
        payload: payload as TeamMemberUpdateRequest,
      });
    } else {
      await createMutation.mutateAsync(payload as TeamMemberCreateRequest);
    }
    closeDrawer();
  };

  const handleIssueKey = async (member: TeamMember) => {
    const issued = await issueKeyMutation.mutateAsync({ memberId: member.id });
    issuedKeyDialog.show(issued.key);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Users className="h-5 w-5 text-primary" />
            Team
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Named members above API keys, with aggregate caps enforced at the proxy.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          disabled={busy}
          onClick={() => createDrawer.show()}
        >
          Add member
        </Button>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      <TeamMemberTable
        members={members}
        busy={busy}
        onEdit={(member) => editDrawer.show(member)}
        onIssueKey={(member) => void handleIssueKey(member)}
        onShowOnboarding={(member) => onboardingDialog.show(member)}
        onDelete={(member) => deleteDialog.show(member)}
      />

      <TeamMemberDrawer
        open={createDrawer.open || editDrawer.open}
        busy={createMutation.isPending || updateMutation.isPending}
        member={editDrawer.data}
        onOpenChange={(open) => {
          if (!open) {
            closeDrawer();
          }
        }}
        onSubmit={handleSubmit}
      />

      <TeamOnboardingDialog
        open={onboardingDialog.open}
        member={onboardingDialog.data ?? null}
        onOpenChange={onboardingDialog.onOpenChange}
      />

      <ApiKeyCreatedDialog
        open={issuedKeyDialog.open}
        apiKey={issuedKeyDialog.data ?? null}
        onOpenChange={issuedKeyDialog.onOpenChange}
      />

      <ConfirmDialog
        open={deleteDialog.open}
        title="Remove team member"
        description="Their keys keep working, but stop counting against this member's caps."
        confirmLabel="Remove"
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(deleteDialog.data.id).finally(() => {
            deleteDialog.hide();
          });
        }}
      />

      <LoadingOverlay visible={membersQuery.isPending} label="Loading team..." />
    </div>
  );
}
