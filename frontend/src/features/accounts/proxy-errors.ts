import { ApiError } from "@/lib/api-client";

/**
 * Map a probe-failure :class:`ApiError` to a human-readable error message.
 *
 * The backend's 422 response carries the typed reason via
 * ``payload.error.reason`` (one of: ``proxy_connect``, ``proxy_auth``,
 * ``tls``, ``upstream_status``, ``timeout``). When the reason is missing
 * or unknown, fall back to the server-provided message.
 */

const REASON_TO_MESSAGE: Record<string, string> = {
  proxy_connect: "Could not reach the proxy. Check the host/port and that the SOCKS5 endpoint is up.",
  proxy_auth: "The proxy rejected the username or password.",
  tls: "TLS handshake to the upstream failed through the proxy.",
  upstream_status: "Upstream rejected the refresh. The account's refresh token may need re-authentication.",
  invalid_response: "OAuth refresh succeeded but returned an incomplete token payload.",
  timeout: "The probe timed out. Increase the timeout or check network reachability.",
};

export function probeReasonFromError(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code !== "proxy_probe_failed") return null;
  const payload = error.payload as { error?: { reason?: string } } | undefined;
  return payload?.error?.reason ?? null;
}

export function formatProbeError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : "Failed to validate proxy";
  }
  if (error.code === "proxy_password_unrecoverable") {
    return "The stored proxy password could not be decrypted (the encryption key may have been rotated). Please re-enter the password to save this configuration.";
  }
  if (error.code === "account_credentials_unrecoverable") {
    return "The account's stored credentials could not be decrypted (the encryption key may have been rotated). Please re-import this account from auth.json.";
  }
  const reason = probeReasonFromError(error);
  if (reason && REASON_TO_MESSAGE[reason]) {
    return REASON_TO_MESSAGE[reason];
  }
  return error.message || "Failed to validate proxy";
}
