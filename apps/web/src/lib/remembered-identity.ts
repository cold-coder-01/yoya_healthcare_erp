export const REMEMBERED_IDENTITY_STORAGE_KEY =
  "yoya.rememberedIdentity.v1";

export type RememberedIdentity = {
  login: string;
  displayName: string;
};

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;

function clean(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function readRememberedIdentity(
  storage: ReadableStorage,
): RememberedIdentity | null {
  try {
    const raw = storage.getItem(REMEMBERED_IDENTITY_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null) {
      return null;
    }

    const record = parsed as Record<string, unknown>;
    const login = clean(record.login);
    const displayName = clean(record.displayName);

    return login && displayName ? { login, displayName } : null;
  } catch {
    return null;
  }
}

export function persistRememberedIdentity(
  storage: WritableStorage,
  loginValue: unknown,
  displayNameValue: unknown,
): RememberedIdentity | null {
  const login = clean(loginValue);
  const displayName = clean(displayNameValue);

  if (!login || !displayName) {
    return null;
  }

  const identity = { login, displayName };

  try {
    storage.setItem(
      REMEMBERED_IDENTITY_STORAGE_KEY,
      JSON.stringify(identity),
    );
    return identity;
  } catch {
    return null;
  }
}

export function matchesRememberedIdentity(
  typedLogin: string,
  identity: RememberedIdentity | null,
) {
  return Boolean(
    identity &&
      typedLogin.trim().toLocaleLowerCase() ===
        identity.login.toLocaleLowerCase(),
  );
}

export function greetingForHour(hour: number) {
  if (hour >= 5 && hour < 12) {
    return "Good morning";
  }
  if (hour >= 12 && hour < 17) {
    return "Good afternoon";
  }
  return "Good evening";
}
