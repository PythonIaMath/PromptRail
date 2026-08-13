import assert from "node:assert/strict";
import test from "node:test";

import {
  decryptProviderCredentials,
  encryptProviderCredentials,
} from "./providerCredentialEnvelope.js";

const KEY = Buffer.alloc(32, 7).toString("base64");

test("provider credential envelopes round-trip without retaining plaintext", () => {
  process.env.PROMPTRAIL_PROVIDER_CREDENTIAL_KEK_V1 = KEY;
  const identity = {
    connectionId: "connection-1",
    userId: "tenant-1",
    provider: "openrouter",
  };
  const envelope = encryptProviderCredentials({
    ...identity,
    credentials: { apiKey: "provider-secret" },
  });
  assert.equal(JSON.stringify(envelope).includes("provider-secret"), false);
  assert.deepEqual(
    decryptProviderCredentials({ ...identity, envelope }),
    { apiKey: "provider-secret" },
  );
});

test("provider credential envelopes are bound to tenant and connection identity", () => {
  process.env.PROMPTRAIL_PROVIDER_CREDENTIAL_KEK_V1 = KEY;
  const envelope = encryptProviderCredentials({
    connectionId: "connection-1",
    userId: "tenant-1",
    provider: "openrouter",
    credentials: { apiKey: "provider-secret" },
  });
  assert.throws(() => decryptProviderCredentials({
    connectionId: "connection-1",
    userId: "tenant-2",
    provider: "openrouter",
    envelope,
  }));
});
