import { del, get, patch, post } from "@/lib/api-client";

import {
  PeerFallbackTargetCreateRequestSchema,
  PeerFallbackTargetDeleteResponseSchema,
  PeerFallbackTargetSchema,
  PeerFallbackTargetsResponseSchema,
  PeerFallbackTargetUpdateRequestSchema,
} from "@/features/peer-fallback-targets/schemas";

const PEER_FALLBACK_TARGETS_PATH = "/api/peer-fallback-targets";

export function listPeerFallbackTargets() {
  return get(PEER_FALLBACK_TARGETS_PATH, PeerFallbackTargetsResponseSchema);
}

export function createPeerFallbackTarget(payload: unknown) {
  const validated = PeerFallbackTargetCreateRequestSchema.parse(payload);
  return post(PEER_FALLBACK_TARGETS_PATH, PeerFallbackTargetSchema, {
    body: validated,
  });
}

export function updatePeerFallbackTarget(targetId: string, payload: unknown) {
  const validated = PeerFallbackTargetUpdateRequestSchema.parse(payload);
  return patch(
    `${PEER_FALLBACK_TARGETS_PATH}/${encodeURIComponent(targetId)}`,
    PeerFallbackTargetSchema,
    {
      body: validated,
    },
  );
}

export function deletePeerFallbackTarget(targetId: string) {
  return del(
    `${PEER_FALLBACK_TARGETS_PATH}/${encodeURIComponent(targetId)}`,
    PeerFallbackTargetDeleteResponseSchema,
  );
}
