import { del, get, patch, post } from "@/lib/api-client";

import {
  ApiKeyCreateRequestSchema,
  ApiKeyCreateResponseSchema,
  ApiKeyListSchema,
  ApiKeySchema,
  ApiKeyUpdateRequestSchema,
  ModelsResponseSchema,
} from "@/features/api-keys/schemas";

export const API_KEYS_BASE_PATH = "/api/api-keys";
// The versioned path is intentionally unknown to pre-policy replicas. This
// makes mixed-version policy writes fail closed instead of losing the field.
const API_KEYS_POLICY_BASE_PATH = `${API_KEYS_BASE_PATH}/v2`;
const MODELS_PATH = "/api/models";

function usesReasoningPolicy(payload: object): boolean {
  return "allowedReasoningEfforts" in payload;
}

export function listApiKeys() {
  return get(`${API_KEYS_BASE_PATH}/`, ApiKeyListSchema);
}

export function createApiKey(payload: unknown) {
  const validated = ApiKeyCreateRequestSchema.parse(payload);
  const basePath = usesReasoningPolicy(validated)
    ? API_KEYS_POLICY_BASE_PATH
    : API_KEYS_BASE_PATH;
  return post(`${basePath}/`, ApiKeyCreateResponseSchema, {
    body: validated,
  });
}

export function updateApiKey(keyId: string, payload: unknown) {
  const validated = ApiKeyUpdateRequestSchema.parse(payload);
  const basePath = usesReasoningPolicy(validated)
    ? API_KEYS_POLICY_BASE_PATH
    : API_KEYS_BASE_PATH;
  return patch(`${basePath}/${encodeURIComponent(keyId)}`, ApiKeySchema, {
    body: validated,
  });
}

export function deleteApiKey(keyId: string) {
  return del(`${API_KEYS_BASE_PATH}/${encodeURIComponent(keyId)}`);
}

export function regenerateApiKey(keyId: string) {
  return post(
    `${API_KEYS_BASE_PATH}/${encodeURIComponent(keyId)}/regenerate`,
    ApiKeyCreateResponseSchema,
  );
}

export function listModels() {
  return get(MODELS_PATH, ModelsResponseSchema);
}
