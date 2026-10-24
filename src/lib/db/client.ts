import type { DatabaseSync } from "node:sqlite";

import { runMigrations } from "./migrations.ts";
import { openDatabase } from "./sqlite.ts";

export function withDatabase<T>(callback: (db: DatabaseSync) => T): T {
  const handle = openDatabase();

  try {
    runMigrations(handle.db);
    return callback(handle.db);
  } finally {
    handle.db.close();
  }
}

export async function withDatabaseAsync<T>(
  callback: (db: DatabaseSync) => Promise<T>,
): Promise<T> {
  const handle = openDatabase();

  try {
    runMigrations(handle.db);
    return await callback(handle.db);
  } finally {
    handle.db.close();
  }
}
