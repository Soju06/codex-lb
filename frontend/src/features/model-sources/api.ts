import { del, get, patch, post } from "@/lib/api-client";

import {
  ModelSourceCreateRequestSchema,
  ModelSourceSchema,
  ModelSourcesResponseSchema,
  ModelSourceUpdateRequestSchema,
} from "@/features/model-sources/schemas";

const MODEL_SOURCES_PATH = "/api/model-sources";

export function listModelSources() {
  return get(`${MODEL_SOURCES_PATH}/`, ModelSourcesResponseSchema);
}

export function createModelSource(payload: unknown) {
  const validated = ModelSourceCreateRequestSchema.parse(payload);
  return post(`${MODEL_SOURCES_PATH}/`, ModelSourceSchema, {
    body: validated,
  });
}

export function updateModelSource(sourceId: string, payload: unknown) {
  const validated = ModelSourceUpdateRequestSchema.parse(payload);
  return patch(`${MODEL_SOURCES_PATH}/${encodeURIComponent(sourceId)}`, ModelSourceSchema, {
    body: validated,
  });
}

export function deleteModelSource(sourceId: string) {
  return del(`${MODEL_SOURCES_PATH}/${encodeURIComponent(sourceId)}`);
}
