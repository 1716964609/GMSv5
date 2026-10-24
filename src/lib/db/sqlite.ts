import { existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { AppError } from "../domain/errors.ts";

export type DatabaseEnvironment = "development" | "test" | "production";

export type DatabaseHandle = {
  db: DatabaseSync;
  path: string;
};

/**
 * Resolve the database location without silently falling back in production.
 *
 * In production, GMS_DATABASE_PATH must be an absolute path configured for the
 * independently mounted data volume. The parent directory must already exist;
 * this prevents a missing mount from being replaced by an accidentally-created
 * directory or database on the root filesystem.
 */
export function resolveDatabasePath(
  configuredPath = process.env.GMS_DATABASE_PATH,
  environment = (process.env.NODE_ENV as DatabaseEnvironment | undefined) ??
    "development",
): string {
  if (configuredPath !== undefined && configuredPath.trim() === "") {
    throw new AppError(503, "DATABASE_NOT_CONFIGURED", "GMS_DATABASE_PATH must not be empty");
  }

  if (environment === "production") {
    if (configuredPath === undefined) {
      throw new AppError(
        503,
        "DATABASE_NOT_CONFIGURED",
        "GMS_DATABASE_PATH must be set to an absolute path in production",
      );
    }

    if (!isAbsolute(configuredPath)) {
      throw new AppError(
        503,
        "DATABASE_NOT_CONFIGURED",
        "GMS_DATABASE_PATH must be an absolute path in production",
      );
    }

    return resolve(configuredPath);
  }

  return resolve(configuredPath ?? ".data/gmsv5.sqlite");
}

export function openDatabase(
  configuredPath?: string,
  environment: DatabaseEnvironment =
    (process.env.NODE_ENV as DatabaseEnvironment | undefined) ?? "development",
): DatabaseHandle {
  const databasePath = resolveDatabasePath(configuredPath, environment);
  const parentDirectory = dirname(databasePath);

  if (environment === "production") {
    if (!existsSync(parentDirectory) || !statSync(parentDirectory).isDirectory()) {
      throw new AppError(
        503,
        "DATABASE_NOT_MOUNTED",
        `Database parent directory does not exist: ${parentDirectory}`,
      );
    }
  } else {
    mkdirSync(parentDirectory, { recursive: true });
  }

  const db = new DatabaseSync(databasePath);
  db.exec("PRAGMA foreign_keys = ON");

  const foreignKeys = db
    .prepare("PRAGMA foreign_keys")
    .get<{ foreign_keys: number }>();

  if (foreignKeys?.foreign_keys !== 1) {
    db.close();
    throw new AppError(
      503,
      "DATABASE_FOREIGN_KEYS_DISABLED",
      "SQLite foreign key enforcement could not be enabled",
    );
  }

  return { db, path: databasePath };
}
