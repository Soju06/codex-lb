import { del, get, patch, post } from "@/lib/api-client";

import {
  ApiKeyCreateRequestSchema,
  ApiKeyCreateResponseSchema,
  ApiKeyListSchema,
  ApiKeySchema,
  ApiKeyUpdateRequestSchema,
  ModelsResponseSchema,
} from "@/features/api-keys/schemas";

const API_KEYS_BASE_PATH = "/api/api-keys";
const MODELS_PATH = "/api/models";

export function listApiKeys(ownerUserId?: string) {
  const query = new URLSearchParams();
  if (ownerUserId) {
    query.set("ownerUserId", ownerUserId);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${API_KEYS_BASE_PATH}/${suffix}`, ApiKeyListSchema);
}

export function createApiKey(payload: unknown) {
  const validated = ApiKeyCreateRequestSchema.parse(payload);
  return post(`${API_KEYS_BASE_PATH}/`, ApiKeyCreateResponseSchema, {
    body: validated,
  });
}

export function updateApiKey(keyId: string, payload: unknown) {
  const validated = ApiKeyUpdateRequestSchema.parse(payload);
  return patch(`${API_KEYS_BASE_PATH}/${encodeURIComponent(keyId)}`, ApiKeySchema, {
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
