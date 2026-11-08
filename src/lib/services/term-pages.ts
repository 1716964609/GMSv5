import type { DatabaseSync } from "node:sqlite";

import { listVersionedTerms, readTermVersion } from "../db/term-pages.ts";
import { AppError } from "../domain/errors.ts";
import { positivePage } from "../validation.ts";

/** Shared by the route and isolated API/DB integration tests. */
export function readTermPageRequest(db: DatabaseSync, userId: string, glossaryId: string, url: URL) {
  const expected = url.searchParams.get("expectedVersion") ?? undefined;
  if (expected !== undefined && !/^[a-f0-9]{32}:[a-f0-9]{32}:\d{1,19}$/.test(expected)) {
    throw new AppError(400, "INVALID_TERM_VERSION", "Invalid term revision");
  }
  const validateOnly = url.searchParams.get("validateOnly");
  if (validateOnly !== null && validateOnly !== "true") {
    throw new AppError(400, "INVALID_INPUT", "validateOnly must be true");
  }
  if (validateOnly === "true") {
    if (!expected) throw new AppError(400, "INVALID_TERM_VERSION", "An expected revision is required");
    return readTermVersion(db, userId, glossaryId, expected);
  }
  return listVersionedTerms(db, userId, glossaryId, positivePage(url.searchParams.get("page")), expected);
}
