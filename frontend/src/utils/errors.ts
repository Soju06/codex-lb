export function getErrorMessage(error: unknown, fallback = "Request failed"): string {
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}

export function getErrorMessageOrNull(error: unknown, fallback = "Request failed"): string | null {
  if (!error) {
    return null;
  }
  return getErrorMessage(error, fallback);
}
