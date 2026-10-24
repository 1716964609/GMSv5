import type { DatabaseSync } from "node:sqlite";

import {
  deleteCredential,
  listCredentialSummaries,
  upsertCredential,
} from "../db/repository.ts";
import { encryptSecret } from "../security/credentials.ts";
import { AppError } from "../domain/errors.ts";
import type { CredentialSummary, Provider } from "../domain/types.ts";
import { requiredString } from "../validation.ts";

export function providerValue(value: unknown): Provider {
  if (value !== "DEEPL" && value !== "OPENAI") {
    throw new AppError(400, "INVALID_PROVIDER", "provider must be DEEPL or OPENAI");
  }
  return value;
}

export function listCredentialsUseCase(
  db: DatabaseSync,
  userId: string,
): CredentialSummary[] {
  return listCredentialSummaries(db, userId);
}

export function saveCredentialUseCase(
  db: DatabaseSync,
  userId: string,
  provider: unknown,
  secret: unknown,
): CredentialSummary {
  const selectedProvider = providerValue(provider);
  const apiKey = requiredString(secret, "apiKey", { maxLength: 20_000 });
  return upsertCredential(db, userId, selectedProvider, encryptSecret(apiKey));
}

export function deleteCredentialUseCase(
  db: DatabaseSync,
  userId: string,
  provider: unknown,
): void {
  deleteCredential(db, userId, providerValue(provider));
}
