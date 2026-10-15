// The login form remembers the last username that signed in so a team-tier
// install prefills it, and so the field stays visible after the second account
// is removed again (anti-flapping, PLAN §4.4). Never holds a password.
export const LAST_USERNAME_STORAGE_KEY = "codex-lb.last-username";

export function readLastUsername(): string {
  try {
    return window.localStorage.getItem(LAST_USERNAME_STORAGE_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

export function rememberLastUsername(username: string | undefined): void {
  try {
    if (username && username.trim()) {
      window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, username.trim());
    } else {
      window.localStorage.removeItem(LAST_USERNAME_STORAGE_KEY);
    }
  } catch {
    /* storage unavailable: prefill is a convenience only */
  }
}
