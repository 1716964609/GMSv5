import { randomBytes } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";

import { AppError } from "../domain/errors.ts";
import type { TermPage } from "../domain/types.ts";
import { findGlossaryForOwner, listTerms } from "./repository.ts";

// Conservative invalidation on server restart/HMR/worker change, including restores.
const readEpoch = randomBytes(16).toString("hex");

function readTransaction<T>(db: DatabaseSync, callback: () => T): T {
  db.exec("BEGIN");
  try {
    const result = callback();
    db.exec("COMMIT");
    return result;
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}

function versionForOwner(db: DatabaseSync, userId: string, glossaryId: string, expected?: string): string {
  if (!findGlossaryForOwner(db, userId, glossaryId)) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  const row = db.prepare(
    "SELECT epoch, CAST(revision AS TEXT) AS revision FROM glossary_term_revisions WHERE glossary_id = ?",
  ).get<{ epoch: string; revision: string }>(glossaryId);
  if (!row) throw new AppError(503, "TERM_REVISION_UNAVAILABLE", "Term revision is unavailable");
  const version = `${readEpoch}:${row.epoch}:${row.revision}`;
  if (expected !== undefined && expected !== version) {
    throw new AppError(409, "TERM_SET_CHANGED", "用語集が更新されました。選択は変更していません。もう一度Select Allを実行してください。");
  }
  return version;
}

export function readTermVersion(db: DatabaseSync, userId: string, glossaryId: string, expected: string) {
  return readTransaction(db, () => ({ termSetVersion: versionForOwner(db, userId, glossaryId, expected) }));
}

export function listVersionedTerms(
  db: DatabaseSync, userId: string, glossaryId: string, page: number, expected?: string,
): TermPage & { termSetVersion: string } {
  return readTransaction(db, () => {
    const termSetVersion = versionForOwner(db, userId, glossaryId, expected);
    return { ...listTerms(db, userId, glossaryId, page), termSetVersion };
  });
}
