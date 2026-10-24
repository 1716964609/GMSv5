import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

import { AppError } from "../domain/errors.ts";

const KEY_LENGTH = 32;
const IV_LENGTH = 12;

function encryptionKey(): Buffer {
  const encoded = process.env.GMS_CREDENTIAL_ENCRYPTION_KEY;
  if (!encoded) {
    throw new AppError(
      503,
      "CREDENTIAL_ENCRYPTION_NOT_CONFIGURED",
      "Credential encryption is not configured",
    );
  }

  const key = Buffer.from(encoded, "base64");
  if (key.length !== KEY_LENGTH) {
    throw new AppError(
      503,
      "CREDENTIAL_ENCRYPTION_INVALID",
      "Credential encryption key must be a base64-encoded 32-byte value",
    );
  }

  return key;
}

export function encryptSecret(secret: string): string {
  if (secret.length === 0) {
    throw new AppError(400, "INVALID_CREDENTIAL", "API credential must not be empty");
  }

  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), iv);
  const encrypted = Buffer.concat([cipher.update(secret, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();

  return [
    "v1",
    iv.toString("base64url"),
    tag.toString("base64url"),
    encrypted.toString("base64url"),
  ].join(":");
}

export function decryptSecret(payload: string): string {
  const [version, ivEncoded, tagEncoded, encryptedEncoded] = payload.split(":");
  if (version !== "v1" || !ivEncoded || !tagEncoded || !encryptedEncoded) {
    throw new AppError(500, "INVALID_ENCRYPTED_CREDENTIAL", "Stored credential format is invalid");
  }

  try {
    const decipher = createDecipheriv(
      "aes-256-gcm",
      encryptionKey(),
      Buffer.from(ivEncoded, "base64url"),
    );
    decipher.setAuthTag(Buffer.from(tagEncoded, "base64url"));
    return Buffer.concat([
      decipher.update(Buffer.from(encryptedEncoded, "base64url")),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw new AppError(500, "CREDENTIAL_DECRYPTION_FAILED", "Stored credential could not be decrypted");
  }
}
