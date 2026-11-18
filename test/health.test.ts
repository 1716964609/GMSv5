import assert from "node:assert/strict";
import { test } from "node:test";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import {
  checkReadiness,
  normalizeRelease,
} from "../src/lib/health/readiness.ts";

import {
  migrations,
  runMigrations,
} from "../src/lib/db/migrations.ts";

const SHA = "a".repeat(40);

function withDatabase(
  callback: (path: string) => void,
  migrate = true,
): void {
  const dir = mkdtempSync(join(tmpdir(), "gms-health-"));
  const path = join(dir, "test.sqlite");

  try {
    const db = new DatabaseSync(path);

    try {
      if (migrate) {
        runMigrations(db);
      }
    } finally {
      db.close();
    }

    callback(path);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("valid release SHA is preserved", () => {
  assert.equal(normalizeRelease(SHA), SHA);
});

test("invalid release SHA is rejected", () => {
  assert.equal(normalizeRelease("invalid"), "unknown");
  assert.equal(normalizeRelease(undefined), "unknown");
});

test("migrated SQLite database is ready", () => {
  withDatabase((path) => {
    assert.deepEqual(checkReadiness(path, SHA), {
      status: "ready",
      release: SHA,
    });
  });
});

test("readiness does not modify database content", () => {
  withDatabase((path) => {
    const before = readFileSync(path);

    assert.equal(checkReadiness(path, SHA).status, "ready");

    assert.deepEqual(readFileSync(path), before);
  });
});

test("missing database is not created by readiness", () => {
  const dir = mkdtempSync(join(tmpdir(), "gms-missing-"));
  const path = join(dir, "missing.sqlite");

  try {
    assert.equal(checkReadiness(path, SHA).status, "not_ready");
    assert.equal(existsSync(path), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("unmigrated database is not ready", () => {
  withDatabase((path) => {
    assert.equal(
      checkReadiness(path, SHA).status,
      "not_ready",
    );
  }, false);
});

test("outdated schema is not migrated by readiness", () => {
  withDatabase((path) => {
    const db = new DatabaseSync(path);

    try {
      runMigrations(db, migrations.slice(0, 1));
    } finally {
      db.close();
    }

    const before = readFileSync(path);

    assert.equal(
      checkReadiness(path, SHA).status,
      "not_ready",
    );

    assert.deepEqual(readFileSync(path), before);
  }, false);
});

test("unexpected future migration is rejected", () => {
  withDatabase((path) => {
    const db = new DatabaseSync(path);

    try {
      db.prepare(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
      ).run(999, "unexpected_migration");
    } finally {
      db.close();
    }

    assert.equal(
      checkReadiness(path, SHA).status,
      "not_ready",
    );
  });
});

test("missing application table is rejected", () => {
  withDatabase((path) => {
    const db = new DatabaseSync(path);

    try {
      db.exec("DROP TABLE glossary_term_revisions");
    } finally {
      db.close();
    }

    assert.equal(
      checkReadiness(path, SHA).status,
      "not_ready",
    );
  });
});

test("missing production database configuration is rejected", () => {
  assert.equal(
    checkReadiness(undefined, SHA).status,
    "not_ready",
  );

  assert.equal(
    checkReadiness("relative.sqlite", SHA).status,
    "not_ready",
  );
});

test("unknown artifact release cannot become ready", () => {
  withDatabase((path) => {
    assert.equal(
      checkReadiness(path, undefined).status,
      "not_ready",
    );
  });
});
