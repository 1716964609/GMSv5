import type { DatabaseSync } from "node:sqlite";

export type Migration = {
  version: number;
  name: string;
  up: (db: DatabaseSync) => void;
};

export type MigrationResult = {
  applied: Array<{ version: number; name: string }>;
  skipped: Array<{ version: number; name: string }>;
};

const CREATE_MIGRATION_HISTORY = `
  CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )
`;

export const migrations: readonly Migration[] = [
  {
    version: 1,
    name: "initial_schema",
    up(db) {
      db.exec(`
        CREATE TABLE users (
          user_id TEXT PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          display_name TEXT,
          auth_subject TEXT NOT NULL UNIQUE,
          email_verified_at TEXT,
          account_status TEXT NOT NULL CHECK (
            account_status IN (
              'PENDING_VERIFICATION',
              'ACTIVE',
              'PENDING_DELETION'
            )
          ),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          deletion_requested_at TEXT,
          deletion_scheduled_at TEXT
        );

        CREATE TABLE glossaries (
          glossary_id TEXT PRIMARY KEY,
          owner_user_id TEXT NOT NULL,
          name TEXT NOT NULL,
          description TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (owner_user_id)
            REFERENCES users (user_id)
            ON DELETE CASCADE
        );

        CREATE TABLE terms (
          term_id TEXT PRIMARY KEY,
          glossary_id TEXT NOT NULL,
          source_language TEXT NOT NULL,
          target_language TEXT NOT NULL,
          source_term TEXT NOT NULL,
          target_term TEXT NOT NULL,
          description TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (glossary_id)
            REFERENCES glossaries (glossary_id)
            ON DELETE CASCADE
        );

        -- source_term and target_term are canonical values produced by the
        -- TypeScript NFC/trim normalization helper before persistence.
        CREATE UNIQUE INDEX terms_glossary_translation_unique
          ON terms (
            glossary_id,
            source_language,
            target_language,
            source_term,
            target_term
          );

        CREATE TABLE api_credentials (
          credential_id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          provider TEXT NOT NULL CHECK (provider IN ('DEEPL', 'OPENAI')),
          encrypted_secret TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (user_id, provider),
          FOREIGN KEY (user_id)
            REFERENCES users (user_id)
            ON DELETE CASCADE
        );
      `);
    },
  },
];

function validateMigrations(migrationSet: readonly Migration[]): void {
  const versions = migrationSet.map((migration) => migration.version);

  if (versions.some((version) => !Number.isInteger(version) || version < 1)) {
    throw new Error("Migration versions must be positive integers");
  }

  if (new Set(versions).size !== versions.length) {
    throw new Error("Migration versions must be unique");
  }
}

export function runMigrations(
  db: DatabaseSync,
  migrationSet: readonly Migration[] = migrations,
): MigrationResult {
  validateMigrations(migrationSet);
  db.exec(CREATE_MIGRATION_HISTORY);

  const appliedRows = db
    .prepare("SELECT version, name FROM schema_migrations ORDER BY version")
    .all<{ version: number; name: string }>();
  const appliedByVersion = new Map(
    appliedRows.map((row) => [row.version, row.name]),
  );
  const applied: MigrationResult["applied"] = [];
  const skipped: MigrationResult["skipped"] = [];

  for (const migration of migrationSet) {
    const appliedName = appliedByVersion.get(migration.version);

    if (appliedName !== undefined) {
      if (appliedName !== migration.name) {
        throw new Error(
          `Migration version ${migration.version} is recorded as ${appliedName}, expected ${migration.name}`,
        );
      }

      skipped.push({ version: migration.version, name: migration.name });
      continue;
    }

    db.exec("BEGIN IMMEDIATE");

    try {
      migration.up(db);
      db.prepare(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
      ).run(migration.version, migration.name);
      db.exec("COMMIT");
      applied.push({ version: migration.version, name: migration.name });
    } catch (error) {
      try {
        db.exec("ROLLBACK");
      } catch {
        // Preserve the original migration error if rollback itself fails.
      }

      throw error;
    }
  }

  return { applied, skipped };
}
