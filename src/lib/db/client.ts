import type { DatabaseSync } from "node:sqlite";

import { isTelemetryEnabled } from "../observability/config.ts";
import { instrumentDatabase } from "../observability/sqlite.ts";
import { runMigrations } from "./migrations.ts";
import { openDatabase, type DatabaseHandle } from "./sqlite.ts";

function openApplicationDatabase(): DatabaseHandle {
  const handle = openDatabase();
  if (isTelemetryEnabled()) {
    instrumentDatabase(handle.db);
  }
  return handle;
}

export function withDatabase<T>(callback: (db: DatabaseSync) => T): T {
  const handle = openApplicationDatabase();

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
  const handle = openApplicationDatabase();

  try {
    runMigrations(handle.db);
    return await callback(handle.db);
  } finally {
    handle.db.close();
  }
}
