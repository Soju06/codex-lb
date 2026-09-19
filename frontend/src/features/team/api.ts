import { del, get, patch, post } from "@/lib/api-client";

import {
  TeamMemberCreateRequestSchema,
  TeamMemberKeyCreateRequestSchema,
  TeamMemberKeyCreateResponseSchema,
  TeamMemberListSchema,
  TeamMemberSchema,
  TeamMemberUpdateRequestSchema,
  TeamMemberUsageSchema,
  TeamOnboardingSchema,
  type TeamWindow,
} from "@/features/team/schemas";

export const TEAM_BASE_PATH = "/api/team";

export function listTeamMembers() {
  return get(`${TEAM_BASE_PATH}/members`, TeamMemberListSchema);
}

export function createTeamMember(payload: unknown) {
  const validated = TeamMemberCreateRequestSchema.parse(payload);
  return post(`${TEAM_BASE_PATH}/members`, TeamMemberSchema, { body: validated });
}

export function updateTeamMember(memberId: string, payload: unknown) {
  const validated = TeamMemberUpdateRequestSchema.parse(payload);
  return patch(`${TEAM_BASE_PATH}/members/${encodeURIComponent(memberId)}`, TeamMemberSchema, {
    body: validated,
  });
}

export function deleteTeamMember(memberId: string) {
  return del(`${TEAM_BASE_PATH}/members/${encodeURIComponent(memberId)}`);
}

export function issueTeamMemberKey(memberId: string, payload: unknown = {}) {
  const validated = TeamMemberKeyCreateRequestSchema.parse(payload);
  return post(
    `${TEAM_BASE_PATH}/members/${encodeURIComponent(memberId)}/keys`,
    TeamMemberKeyCreateResponseSchema,
    { body: validated },
  );
}

export function getTeamMemberUsage(memberId: string, window: TeamWindow) {
  return get(
    `${TEAM_BASE_PATH}/members/${encodeURIComponent(memberId)}/usage?window=${window}`,
    TeamMemberUsageSchema,
  );
}

export function getTeamMemberOnboarding(memberId: string) {
  return get(
    `${TEAM_BASE_PATH}/members/${encodeURIComponent(memberId)}/onboarding`,
    TeamOnboardingSchema,
  );
}
