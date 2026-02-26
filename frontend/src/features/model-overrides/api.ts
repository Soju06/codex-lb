import { del, get, post, put } from "@/lib/api-client";

import {
  ModelOverrideSchema,
  ModelOverridesResponseSchema,
  type ModelOverrideCreateRequest,
  type ModelOverrideUpdateRequest,
} from "@/features/model-overrides/schemas";

const MODEL_OVERRIDES_PATH = "/api/model-overrides";

export async function listModelOverrides() {
  const response = await get(MODEL_OVERRIDES_PATH, ModelOverridesResponseSchema);
  return response.items;
}

export function createModelOverride(payload: ModelOverrideCreateRequest) {
  return post(MODEL_OVERRIDES_PATH, ModelOverrideSchema, { body: payload });
}

export function updateModelOverride(overrideId: number, payload: ModelOverrideUpdateRequest) {
  return put(`${MODEL_OVERRIDES_PATH}/${overrideId}`, ModelOverrideSchema, { body: payload });
}

export function deleteModelOverride(overrideId: number) {
  return del(`${MODEL_OVERRIDES_PATH}/${overrideId}`);
}

