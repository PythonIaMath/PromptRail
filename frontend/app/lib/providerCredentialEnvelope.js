import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
} from "node:crypto";
import { serverEnv } from "./serverEnv.js";

const ALGORITHM = "aes-256-gcm";
const IV_BYTES = 12;

function keyForVersion(version) {
  const encoded = serverEnv(`PROMPTRAIL_PROVIDER_CREDENTIAL_KEK_V${version}`);
  if (!encoded) {
    throw new Error(`Provider credential key version ${version} is not configured.`);
  }
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32) {
    throw new Error(`Provider credential key version ${version} must decode to 32 bytes.`);
  }
  return key;
}

function associatedData({ connectionId, userId, provider, keyVersion }) {
  return Buffer.from(
    JSON.stringify({ connectionId, userId, provider, keyVersion }),
    "utf8",
  );
}

export function encryptProviderCredentials({
  connectionId,
  userId,
  provider,
  credentials,
  keyVersion = 1,
}) {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv(ALGORITHM, keyForVersion(keyVersion), iv);
  cipher.setAAD(associatedData({ connectionId, userId, provider, keyVersion }));
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(credentials), "utf8"),
    cipher.final(),
  ]);
  return {
    version: 1,
    keyVersion,
    algorithm: ALGORITHM,
    iv: iv.toString("base64"),
    ciphertext: ciphertext.toString("base64"),
    authTag: cipher.getAuthTag().toString("base64"),
  };
}

export function decryptProviderCredentials({ connectionId, userId, provider, envelope }) {
  if (envelope?.version !== 1 || envelope?.algorithm !== ALGORITHM) {
    throw new Error("Unsupported provider credential envelope.");
  }
  const keyVersion = Number(envelope.keyVersion);
  const decipher = createDecipheriv(
    ALGORITHM,
    keyForVersion(keyVersion),
    Buffer.from(envelope.iv, "base64"),
  );
  decipher.setAAD(associatedData({ connectionId, userId, provider, keyVersion }));
  decipher.setAuthTag(Buffer.from(envelope.authTag, "base64"));
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(envelope.ciphertext, "base64")),
    decipher.final(),
  ]);
  return JSON.parse(plaintext.toString("utf8"));
}
