import { get } from "@/lib/api-client";

import { RuntimeVersionSchema } from "@/features/runtime/schemas";

const RUNTIME_VERSION_PATH = "/api/runtime/version";

export function getRuntimeVersion() {
  return get(RUNTIME_VERSION_PATH, RuntimeVersionSchema);
}
