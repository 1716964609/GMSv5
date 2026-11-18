import { statSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

import { migrations } from "../db/migrations.ts";
import { resolveDatabasePath } from "../db/sqlite.ts";

export type ReadinessResult = {
  status: "ready" | "not_ready";
  release: string;
};

export function normalizeRelease(value?: string): string {
  return /^[a-f0-9]{40}$/.test(value ?? "")
    ? value!
    : "unknown";
}

export function checkReadiness(
  databasePath: string | undefined,
  releaseValue: string | undefined,
): ReadinessResult {
  const release = normalizeRelease(releaseValue);

  const failure: ReadinessResult = {
    status: "not_ready",
    release,
  };

  if (release === "unknown") {
    return failure;
  }

  let db: DatabaseSync | undefined;

  try {
    const path = resolveDatabasePath(
      databasePath,
      "production",
    );

    if (!statSync(path).isFile()) {
      return failure;
    }

    db = new DatabaseSync(path, {
      readOnly: true,
    });

    db.exec("PRAGMA query_only = ON");
    db.exec("PRAGMA busy_timeout = 250");

    const rows = db.prepare(
      "SELECT version, name FROM schema_migrations ORDER BY version",
    ).all();

    if (rows.length !== migrations.length) {
      return failure;
    }

    const applied = new Map(
      rows.map((row) => [row.version, row.name]),
    );

    for (const migration of migrations) {
      if (applied.get(migration.version) !== migration.name) {
        return failure;
      }
    }

    for (const table of [
      "users",
      "glossaries",
      "terms",
      "api_credentials",
      "glossary_term_revisions",
    ]) {
      db.prepare(
        `SELECT 1 FROM ${table} LIMIT 1`,
      ).get();
    }

    return {
      status: "ready",
      release,
    };
  } catch {
    return failure;
  } finally {
    db?.close();
  }
}
