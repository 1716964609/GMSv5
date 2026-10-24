import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import { runMigrations } from "../src/lib/db/migrations.ts";
import { normalizeTermPair } from "../src/lib/db/normalization.ts";
import {
  openDatabase,
  resolveDatabasePath,
  type DatabaseHandle,
} from "../src/lib/db/sqlite.ts";

const handles: DatabaseHandle[] = [];

afterEach(() => {
  for (const handle of handles.splice(0)) {
    handle.db.close();
    rmSync(handle.path, { force: true });
    rmSync(join(handle.path, ".."), { recursive: true, force: true });
  }
});

function openMigratedDatabase(): DatabaseHandle {
  const directory = mkdtempSync(join(tmpdir(), "gmsv5-db-test-"));
  const handle = openDatabase(join(directory, "gmsv5.sqlite"), "test");
  runMigrations(handle.db);
  handles.push(handle);
  return handle;
}

function insertUser(
  handle: DatabaseHandle,
  userId = "user-1",
): void {
  handle.db
    .prepare(
      `INSERT INTO users (user_id, email, auth_subject, account_status)
       VALUES (?, ?, ?, ?)`,
    )
    .run(userId, `${userId}@example.test`, `cognito-${userId}`, "ACTIVE");
}

function insertGlossary(
  handle: DatabaseHandle,
  glossaryId = "glossary-1",
  userId = "user-1",
): void {
  handle.db
    .prepare(
      `INSERT INTO glossaries (glossary_id, owner_user_id, name)
       VALUES (?, ?, ?)`,
    )
    .run(glossaryId, userId, "Test glossary");
}

function insertTerm(
  handle: DatabaseHandle,
  termId: string,
  sourceTerm: string,
  targetTerm: string,
): void {
  const normalized = normalizeTermPair(sourceTerm, targetTerm);
  handle.db
    .prepare(
      `INSERT INTO terms (
        term_id,
        glossary_id,
        source_language,
        target_language,
        source_term,
        target_term
      ) VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .run(
      termId,
      "glossary-1",
      "JA",
      "EN",
      normalized.sourceTerm,
      normalized.targetTerm,
    );
}

describe("SQLite database migration", () => {
  it("applies to an empty database and is safe to re-run", () => {
    const handle = openMigratedDatabase();
    const secondRun = runMigrations(handle.db);
    const migrationCount = handle.db
      .prepare("SELECT COUNT(*) AS count FROM schema_migrations")
      .get<{ count: number }>();

    assert.equal(migrationCount?.count, 1);
    assert.deepEqual(secondRun.applied, []);
    assert.deepEqual(secondRun.skipped, [
      { version: 1, name: "initial_schema" },
    ]);
  });

  it("rolls back a failed migration before allowing a safe retry", () => {
    const directory = mkdtempSync(join(tmpdir(), "gmsv5-db-test-"));
    const handle = openDatabase(join(directory, "gmsv5.sqlite"), "test");
    handles.push(handle);

    assert.throws(
      () =>
        runMigrations(handle.db, [
          {
            version: 1,
            name: "deliberately_failing",
            up(db) {
              db.exec("CREATE TABLE should_be_rolled_back (id TEXT)");
              throw new Error("deliberate migration failure");
            },
          },
        ]),
      /deliberate migration failure/,
    );

    const rolledBackTable = handle.db
      .prepare(
        `SELECT COUNT(*) AS count FROM sqlite_master
         WHERE type = 'table' AND name = 'should_be_rolled_back'`,
      )
      .get<{ count: number }>();
    const migrationCount = handle.db
      .prepare("SELECT COUNT(*) AS count FROM schema_migrations")
      .get<{ count: number }>();

    assert.equal(rolledBackTable?.count, 0);
    assert.equal(migrationCount?.count, 0);
    assert.deepEqual(runMigrations(handle.db).applied, [
      { version: 1, name: "initial_schema" },
    ]);
  });

  it("enforces foreign keys and cascades glossary deletion to terms", () => {
    const handle = openMigratedDatabase();
    insertUser(handle);
    insertGlossary(handle);
    insertTerm(handle, "term-1", "心不全", "heart failure");

    assert.throws(() => {
      handle.db
        .prepare(
          `INSERT INTO glossaries (glossary_id, owner_user_id, name)
           VALUES (?, ?, ?)`,
        )
        .run("orphan-glossary", "missing-user", "Invalid");
    }, /FOREIGN KEY constraint failed/);

    handle.db
      .prepare("DELETE FROM glossaries WHERE glossary_id = ?")
      .run("glossary-1");
    const remainingTerms = handle.db
      .prepare("SELECT COUNT(*) AS count FROM terms")
      .get<{ count: number }>();

    assert.equal(remainingTerms?.count, 0);
  });

  it("rejects exact normalized duplicates but allows different translations", () => {
    const handle = openMigratedDatabase();
    insertUser(handle);
    insertGlossary(handle);

    insertTerm(handle, "term-1", " 心不全 ", " cardiac failure ");
    assert.doesNotThrow(() => {
      insertTerm(handle, "term-2", "心不全", "heart failure");
    });

    assert.throws(() => {
      insertTerm(handle, "term-3", "心不全", "  cardiac failure  ");
    }, /UNIQUE constraint failed/);

    insertTerm(handle, "term-4", " cafe\u0301 ", "résumé");
    assert.throws(() => {
      insertTerm(handle, "term-5", "café", "résumé");
    }, /UNIQUE constraint failed/);

    const termCount = handle.db
      .prepare("SELECT COUNT(*) AS count FROM terms")
      .get<{ count: number }>();
    assert.equal(termCount?.count, 3);
  });

  it("enforces one API credential per user and provider", () => {
    const handle = openMigratedDatabase();
    insertUser(handle);

    const insertCredential = (credentialId: string, provider: string) => {
      handle.db
        .prepare(
          `INSERT INTO api_credentials
            (credential_id, user_id, provider, encrypted_secret)
           VALUES (?, ?, ?, ?)`,
        )
        .run(credentialId, "user-1", provider, "encrypted-value");
    };

    insertCredential("credential-deepl", "DEEPL");
    insertCredential("credential-openai", "OPENAI");
    assert.throws(() => {
      insertCredential("credential-deepl-duplicate", "DEEPL");
    }, /UNIQUE constraint failed/);
    assert.throws(() => {
      insertCredential("credential-invalid", "OTHER");
    }, /CHECK constraint failed/);
  });

  it("rejects invalid required data and excludes non-persistent objects", () => {
    const handle = openMigratedDatabase();
    const tables = handle.db
      .prepare(
        `SELECT name FROM sqlite_master
         WHERE type = 'table' ORDER BY name`,
      )
      .all<{ name: string }>()
      .map(({ name }) => name);

    assert.deepEqual(tables, [
      "api_credentials",
      "glossaries",
      "schema_migrations",
      "terms",
      "users",
    ]);
    assert.equal(tables.includes("translation_requests"), false);
    assert.equal(tables.includes("translation_contexts"), false);
    assert.equal(tables.includes("translation_results"), false);
    assert.throws(() => {
      handle.db.prepare("SELECT password_hash FROM users").get();
    }, /no such column/);

    assert.throws(() => {
      handle.db
        .prepare(
          `INSERT INTO users (user_id, email, auth_subject, account_status)
           VALUES (?, ?, ?, ?)`,
        )
        .run("invalid-user", "invalid@example.test", "subject", "DELETED");
    }, /CHECK constraint failed/);

    assert.throws(() => {
      handle.db
        .prepare(
          `INSERT INTO users (user_id, email, account_status)
           VALUES (?, ?, ?)`,
        )
        .run("missing-auth-subject", "missing@example.test", "ACTIVE");
    }, /NOT NULL constraint failed/);
  });

  it("requires an explicit absolute production database path", () => {
    assert.throws(
      () => resolveDatabasePath(undefined, "production"),
      /GMS_DATABASE_PATH must be set/,
    );
    assert.throws(
      () => resolveDatabasePath("relative.sqlite", "production"),
      /absolute path/,
    );
  });
});
