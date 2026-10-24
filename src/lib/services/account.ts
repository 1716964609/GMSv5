import type { DatabaseSync } from "node:sqlite";

import { requestAccountDeletion, updateDisplayName } from "../db/repository.ts";
import { AppError } from "../domain/errors.ts";
import type { User } from "../domain/types.ts";
import { optionalString } from "../validation.ts";

export function updateProfileUseCase(
  db: DatabaseSync,
  userId: string,
  value: unknown,
): User {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new AppError(400, "INVALID_JSON", "Profile must be a JSON object");
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  if (keys.some((key) => key !== "displayName")) {
    throw new AppError(400, "UNKNOWN_FIELD", "Only displayName can be changed");
  }
  const displayName = optionalString(record.displayName, "displayName", {
    maxLength: 200,
  });
  return updateDisplayName(db, userId, displayName ?? "");
}

export function requestDeletionUseCase(db: DatabaseSync, userId: string): User {
  return requestAccountDeletion(db, userId);
}
