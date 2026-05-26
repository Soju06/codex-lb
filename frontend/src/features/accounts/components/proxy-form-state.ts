import {
  AccountProxyInputSchema,
  type AccountProxyInput,
} from "@/features/accounts/schemas";

export type ProxyFormValues = {
  quickPaste: string;
  host: string;
  portText: string;
  username: string;
  password: string;
  remoteDns: boolean;
  label: string;
};

export const DEFAULT_PROXY_FORM_VALUES: ProxyFormValues = {
  quickPaste: "",
  host: "",
  portText: "1080",
  username: "",
  password: "",
  remoteDns: true,
  label: "",
};

const PROXY_URI_RE = /^(?:socks5[h]??:\/\/)?([^:@\s]+):([^@\s]*)@([^:\s]+):(\d+)$/;

export function parseQuickPaste(value: string): Partial<ProxyFormValues> | null {
  const match = value.trim().match(PROXY_URI_RE);
  if (!match) return null;
  const [, username, password, host, port] = match;
  return { username, password, host, portText: port };
}

export function parseProxyPort(portText: string): number {
  const trimmed = portText.trim();
  if (!trimmed) return Number.NaN;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && Number.isInteger(parsed) ? parsed : Number.NaN;
}

export type ProxyValidation =
  | { ok: true; payload: AccountProxyInput; error: null }
  | { ok: false; payload: null; error: string | null };

export function validateProxyForm(values: ProxyFormValues): ProxyValidation {
  const port = parseProxyPort(values.portText);
  const candidate = {
    host: values.host,
    port,
    username: values.username || undefined,
    password: values.password.trim() ? values.password : undefined,
    remoteDns: values.remoteDns,
    label: values.label || undefined,
  };
  const parsed = AccountProxyInputSchema.safeParse(candidate);
  if (parsed.success) {
    return { ok: true, payload: parsed.data, error: null };
  }
  return {
    ok: false,
    payload: null,
    error: parsed.error.issues[0]?.message ?? "Invalid proxy input",
  };
}
