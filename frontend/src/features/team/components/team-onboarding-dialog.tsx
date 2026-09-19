import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useTeamOnboarding } from "@/features/team/hooks/use-team";
import type { TeamMember } from "@/features/team/schemas";

export type TeamOnboardingDialogProps = {
  open: boolean;
  member: TeamMember | null;
  onOpenChange: (open: boolean) => void;
};

export function TeamOnboardingDialog({ open, member, onOpenChange }: TeamOnboardingDialogProps) {
  const onboardingQuery = useTeamOnboarding(open && member ? member.id : null);
  const snippets = onboardingQuery.data?.snippets;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Onboarding</DialogTitle>
          <DialogDescription>
            Send these to {member?.name ?? "the member"} with the key you issued. Replace
            <code className="px-1">&lt;key&gt;</code> with their key.
          </DialogDescription>
        </DialogHeader>

        {snippets ? (
          <div className="space-y-4">
            <OnboardingSnippet title="macOS / Linux (zsh)" value={snippets.macosZsh} />
            <OnboardingSnippet title="Windows (PowerShell)" value={snippets.windowsPowershell} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Loading onboarding snippets...</p>
        )}

        <DialogFooter>
          <Button type="button" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OnboardingSnippet({ title, value }: { title: string; value: string }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{title}</p>
        <CopyButton value={value} />
      </div>
      <pre className="overflow-x-auto rounded-lg border bg-muted/20 px-3 py-2 font-mono text-xs">
        {value}
      </pre>
    </div>
  );
}
