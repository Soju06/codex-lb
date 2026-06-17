import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  UpstreamProxyEndpoint,
  UpstreamProxyPool,
  UpstreamProxyPoolMemberRequest,
} from "@/features/settings/schemas";

export type ProxyPoolMemberDialogProps = {
  open: boolean;
  busy: boolean;
  pools: UpstreamProxyPool[];
  endpoints: UpstreamProxyEndpoint[];
  onOpenChange: (open: boolean) => void;
  onSubmit: (poolId: string, payload: UpstreamProxyPoolMemberRequest) => Promise<unknown>;
};

type ProxyPoolMemberFormProps = {
  busy: boolean;
  pools: UpstreamProxyPool[];
  endpoints: UpstreamProxyEndpoint[];
  onClose: () => void;
  onSubmit: (poolId: string, payload: UpstreamProxyPoolMemberRequest) => Promise<unknown>;
};

function ProxyPoolMemberForm({ busy, pools, endpoints, onClose, onSubmit }: ProxyPoolMemberFormProps) {
  const [poolId, setPoolId] = useState(pools[0]?.id ?? "");
  const [endpointId, setEndpointId] = useState(endpoints[0]?.id ?? "");

  const selectedPool = pools.find((pool) => pool.id === poolId) ?? null;
  const alreadyPresent = selectedPool?.endpointIds.includes(endpointId) ?? false;
  const valid = Boolean(poolId && endpointId && !alreadyPresent);

  const handleSubmit = async () => {
    if (!valid) {
      return;
    }

    try {
      await onSubmit(poolId, {
        endpointId,
        sortOrder: selectedPool?.endpointIds.length ?? 0,
        weight: 1,
        isActive: true,
      });
    } catch {
      return;
    }

    onClose();
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void handleSubmit();
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="proxy-member-pool">Pool</Label>
        <Select value={poolId} onValueChange={setPoolId} disabled={busy || pools.length === 0}>
          <SelectTrigger id="proxy-member-pool" className="w-full">
            <SelectValue placeholder="Select pool" />
          </SelectTrigger>
          <SelectContent>
            {pools.map((pool) => (
              <SelectItem key={pool.id} value={pool.id}>
                {pool.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="proxy-member-endpoint">Endpoint</Label>
        <Select value={endpointId} onValueChange={setEndpointId} disabled={busy || endpoints.length === 0}>
          <SelectTrigger id="proxy-member-endpoint" className="w-full">
            <SelectValue placeholder="Select endpoint" />
          </SelectTrigger>
          <SelectContent>
            {endpoints.map((endpoint) => (
              <SelectItem key={endpoint.id} value={endpoint.id}>
                {endpoint.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {alreadyPresent && selectedPool ? (
        <AlertMessage variant="warning">Endpoint is already in {selectedPool.name}.</AlertMessage>
      ) : null}

      <DialogFooter className="mt-2">
        <Button type="submit" disabled={busy || !valid}>
          Add member
        </Button>
      </DialogFooter>
    </form>
  );
}

export function ProxyPoolMemberDialog({
  open,
  busy,
  pools,
  endpoints,
  onOpenChange,
  onSubmit,
}: ProxyPoolMemberDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add pool member</DialogTitle>
            <DialogDescription>Attach an existing endpoint to a proxy pool.</DialogDescription>
          </DialogHeader>
          <ProxyPoolMemberForm
            busy={busy}
            pools={pools}
            endpoints={endpoints}
            onClose={() => onOpenChange(false)}
            onSubmit={onSubmit}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
