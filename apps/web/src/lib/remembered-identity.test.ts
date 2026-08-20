import assert from "node:assert/strict";
import test from "node:test";

import {
  greetingForHour,
  matchesRememberedIdentity,
  persistRememberedIdentity,
  readRememberedIdentity,
  REMEMBERED_IDENTITY_STORAGE_KEY,
} from "./remembered-identity.ts";

function memoryStorage(initial: string | null = null) {
  let value = initial;

  return {
    getItem(key: string) {
      return key === REMEMBERED_IDENTITY_STORAGE_KEY ? value : null;
    },
    setItem(key: string, nextValue: string) {
      assert.equal(key, REMEMBERED_IDENTITY_STORAGE_KEY);
      value = nextValue;
    },
    value() {
      return value;
    },
  };
}

test("persists only the remembered login and display name", () => {
  const storage = memoryStorage();
  const identity = persistRememberedIdentity(
    storage,
    " hana@doctor ",
    " Hana Bekele ",
  );

  assert.deepEqual(identity, {
    login: "hana@doctor",
    displayName: "Hana Bekele",
  });
  assert.deepEqual(JSON.parse(storage.value() ?? ""), {
    login: "hana@doctor",
    displayName: "Hana Bekele",
  });
});

test("does not persist incomplete identity data", () => {
  const storage = memoryStorage();

  assert.equal(persistRememberedIdentity(storage, "hana@doctor", ""), null);
  assert.equal(storage.value(), null);
});

test("reads only a valid remembered identity", () => {
  const storage = memoryStorage(
    JSON.stringify({
      login: "hana@doctor",
      displayName: "Hana Bekele",
      role: "doctor",
    }),
  );

  assert.deepEqual(readRememberedIdentity(storage), {
    login: "hana@doctor",
    displayName: "Hana Bekele",
  });
  assert.equal(readRememberedIdentity(memoryStorage("{invalid")), null);
});

test("recognition follows the typed login and hides on change", () => {
  const identity = {
    login: "hana@doctor",
    displayName: "Hana Bekele",
  };

  assert.equal(matchesRememberedIdentity("HANA@DOCTOR", identity), true);
  assert.equal(matchesRememberedIdentity("other@doctor", identity), false);
  assert.equal(matchesRememberedIdentity("", identity), false);
});

test("greeting follows browser-local hour boundaries", () => {
  assert.equal(greetingForHour(5), "Good morning");
  assert.equal(greetingForHour(11), "Good morning");
  assert.equal(greetingForHour(12), "Good afternoon");
  assert.equal(greetingForHour(16), "Good afternoon");
  assert.equal(greetingForHour(17), "Good evening");
  assert.equal(greetingForHour(4), "Good evening");
});
