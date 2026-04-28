function createLocalStorageMock(): Storage {
  const store = new Map<string, string>();

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

function readLocalStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export function ensureLocalStorageShim(): void {
  if (typeof window === "undefined") {
    return;
  }

  const storage = readLocalStorage();
  if (storage && typeof storage.clear === "function") {
    return;
  }

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: createLocalStorageMock(),
  });
}
