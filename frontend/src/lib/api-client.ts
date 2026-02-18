import { ApiErrorResponseSchema } from "@/schemas/api";
import { z, type ZodType } from "zod";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

type RequestOptions = {
  body?: unknown;
  headers?: HeadersInit;
  signal?: AbortSignal;
  credentials?: RequestCredentials;
};

const JSON_CONTENT_TYPE = "application/json";
const EMPTY_RESPONSE_STATUS = new Set([204, 205]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly payload: unknown;

  constructor(params: {
    message: string;
    status: number;
    code: string;
    details?: unknown;
    payload?: unknown;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.details = params.details;
    this.payload = params.payload;
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function isBodyInit(value: unknown): value is BodyInit {
  return (
    typeof value === "string" ||
    value instanceof Blob ||
    value instanceof FormData ||
    value instanceof URLSearchParams ||
    value instanceof ArrayBuffer ||
    ArrayBuffer.isView(value) ||
    value instanceof ReadableStream
  );
}

function buildRequestBody(body: unknown): {
  body?: BodyInit;
  contentType?: string;
} {
  if (body === undefined || body === null) {
    return {};
  }
  if (isBodyInit(body)) {
    return { body };
  }
  return {
    body: JSON.stringify(body),
    contentType: JSON_CONTENT_TYPE,
  };
}

async function readJsonPayload(response: Response): Promise<unknown> {
  if (EMPTY_RESPONSE_STATUS.has(response.status)) {
    return undefined;
  }
  const text = await response.text();
  if (!text) {
    return undefined;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function parseApiErrorPayload(payload: unknown): {
  code: string;
  message: string;
  details: unknown;
} {
  const parsed = ApiErrorResponseSchema.safeParse(payload);
  if (!parsed.success) {
    return {
      code: "request_failed",
      message: "Request failed",
      details: payload,
    };
  }

  const error = parsed.data.error;
  const code =
    typeof error.code === "string" && error.code.length > 0 ? error.code : "request_failed";
  const message =
    typeof error.message === "string" && error.message.length > 0
      ? error.message
      : "Request failed";

  return {
    code,
    message,
    details: error,
  };
}

async function request(
  method: HttpMethod,
  url: string,
  schema: null,
  options?: RequestOptions,
): Promise<void>;
async function request<T>(
  method: HttpMethod,
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T>;
async function request<T>(
  method: HttpMethod,
  url: string,
  schema: ZodType<T> | null,
  options?: RequestOptions,
): Promise<T | void> {
  const requestBody = buildRequestBody(options?.body);
  const headers = new Headers(options?.headers);
  if (requestBody.contentType && !headers.has("Content-Type")) {
    headers.set("Content-Type", requestBody.contentType);
  }
  if (!headers.has("Accept")) {
    headers.set("Accept", JSON_CONTENT_TYPE);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      body: requestBody.body,
      headers,
      signal: options?.signal,
      credentials: options?.credentials ?? "same-origin",
    });
  } catch (error) {
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: error instanceof Error ? error.message : "Network request failed",
      details: error,
    });
  }

  if (response.status === 401) {
    unauthorizedHandler?.();
  }

  const payload = await readJsonPayload(response);
  if (!response.ok) {
    const parsedError = parseApiErrorPayload(payload);
    throw new ApiError({
      status: response.status,
      code: parsedError.code,
      message: parsedError.message,
      details: parsedError.details,
      payload,
    });
  }

  if (schema === null) {
    return undefined;
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    if (import.meta.env.DEV) {
      console.error(`Zod schema mismatch for ${method} ${url}`, parsed.error.format(), payload);
    }
    throw new ApiError({
      status: response.status,
      code: "invalid_response_schema",
      message: "Response schema mismatch",
      details: parsed.error.format(),
      payload,
    });
  }

  return parsed.data;
}

export function get<T>(
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T> {
  return request("GET", url, schema, options);
}

export function post<T>(
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T> {
  return request("POST", url, schema, options);
}

export function patch<T>(
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T> {
  return request("PATCH", url, schema, options);
}

export function put<T>(
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T> {
  return request("PUT", url, schema, options);
}

export function del(
  url: string,
  options?: RequestOptions,
): Promise<void>;
export function del<T>(
  url: string,
  schema: ZodType<T>,
  options?: RequestOptions,
): Promise<T>;
export function del<T>(
  url: string,
  schemaOrOptions?: ZodType<T> | RequestOptions,
  maybeOptions?: RequestOptions,
): Promise<T | void> {
  if (schemaOrOptions instanceof z.ZodType) {
    return request("DELETE", url, schemaOrOptions, maybeOptions);
  }
  return request("DELETE", url, null, schemaOrOptions);
}
