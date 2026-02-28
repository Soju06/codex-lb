import { del, get, post } from "@/lib/api-client";

import {
  FirewallDeleteResponseSchema,
  FirewallIpCreateRequestSchema,
  FirewallIpEntrySchema,
  FirewallIpsResponseSchema,
} from "@/features/firewall/schemas";

const FIREWALL_IPS_PATH = "/api/firewall/ips";

export function listFirewallIps() {
  return get(FIREWALL_IPS_PATH, FirewallIpsResponseSchema);
}

export function createFirewallIp(payload: unknown) {
  const validated = FirewallIpCreateRequestSchema.parse(payload);
  return post(FIREWALL_IPS_PATH, FirewallIpEntrySchema, {
    body: validated,
  });
}

export function deleteFirewallIp(ipAddress: string) {
  return del(
    `${FIREWALL_IPS_PATH}/${encodeURIComponent(ipAddress)}`,
    FirewallDeleteResponseSchema,
  );
}
