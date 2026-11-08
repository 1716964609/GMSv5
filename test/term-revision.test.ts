import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { DatabaseSync } from "node:sqlite";
import { afterEach, test } from "node:test";

import { migrations, runMigrations } from "../src/lib/db/migrations.ts";
import { openDatabase, type DatabaseHandle } from "../src/lib/db/sqlite.ts";
import { createGlossary, updateGlossary, createTerm, updateTerm, deleteTerm } from "../src/lib/db/repository.ts";
import { importGlossaryUseCase } from "../src/lib/services/glossary.ts";
import { readTermPageRequest } from "../src/lib/services/term-pages.ts";
import { listVersionedTerms } from "../src/lib/db/term-pages.ts";
import { AppError } from "../src/lib/domain/errors.ts";
import { executeSelectAll } from "../src/lib/translation/bulk-term-selection.ts";
import { TermFetchScheduler } from "../src/lib/translation/term-fetch-scheduler.ts";

const handles: DatabaseHandle[] = [];
const directories: string[] = [];
afterEach(() => {
  for (const handle of handles.splice(0)) handle.db.close();
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
});

function fixture(count = 201, legacy = false) {
  const directory = mkdtempSync(join(tmpdir(), "gms-term-revision-")); directories.push(directory);
  const reader = openDatabase(join(directory, "test.sqlite"), "test"); handles.push(reader);
  runMigrations(reader.db, legacy ? [migrations[0]] : migrations);
  for (const user of ["owner", "other"]) reader.db.prepare(
    "INSERT INTO users (user_id, email, auth_subject, account_status) VALUES (?, ?, ?, 'ACTIVE')",
  ).run(user, `${user}@example.test`, `subject-${user}`);
  const glossary = createGlossary(reader.db, "owner", "Synthetic terms", null);
  reader.db.exec("BEGIN");
  const insert = reader.db.prepare("INSERT INTO terms (term_id, glossary_id, source_language, target_language, source_term, target_term) VALUES (?, ?, 'JA', 'EN', ?, ?)");
  for (let i = 0; i < count; i++) insert.run(`term-${i}`, glossary.glossaryId, `用語${i}`, `term ${i}`);
  reader.db.exec("COMMIT");
  const writer = openDatabase(reader.path, "test"); handles.push(writer);
  const request = (params: Record<string, string> = {}, user = "owner") => readTermPageRequest(
    reader.db, user, glossary.glossaryId, new URL(`http://local.test/api/glossaries/${glossary.glossaryId}/terms?${new URLSearchParams(params)}`),
  );
  const page = (number = 1, version?: string) => {
    const result = request({ page: String(number), ...(version ? { expectedVersion: version } : {}) });
    assert.ok("items" in result);
    return result as ReturnType<typeof listVersionedTerms>;
  };
  const validate = async (version: string) => request({ validateOnly: "true", expectedVersion: version });
  const add = () => createTerm(writer.db, "owner", { glossaryId: glossary.glossaryId, sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "追加用語", targetTerm: "added term", description: null });
  return { reader, writer, glossary, page, request, validate, add };
}
function changed(error: unknown) {
  return error instanceof AppError && error.status === 409 && error.code === "TERM_SET_CHANGED";
}

function operation(f: ReturnType<typeof fixture>, fetchPage = async (number: number, _signal: AbortSignal, version?: string) => f.page(number, version)) {
  let selected = new Set(["previous"]);
  let commits = 0;
  return {
    start: () => executeSelectAll(fetchPage, new AbortController().signal, {
      scheduler: new TermFetchScheduler(), concurrency: 4, validateVersion: f.validate,
      isCurrent: () => true, getSelected: () => selected,
      commit: (ids) => { commits++; selected = ids; },
    }),
    selected: () => selected, commits: () => commits,
  };
}

test("migration v1 -> v2 preserves existing users/glossaries/Terms and backfills revisions", () => {
  const f = fixture(12, true);
  const before = ["users", "glossaries", "terms"].map((table) => f.reader.db.prepare(`SELECT * FROM ${table}`).all());
  assert.deepEqual(runMigrations(f.reader.db).applied, [{ version: 2, name: "glossary_term_revisions" }]);
  assert.deepEqual(["users", "glossaries", "terms"].map((table) => f.reader.db.prepare(`SELECT * FROM ${table}`).all()), before);
  assert.equal(f.page().termSetVersion.split(":").at(-1), "0");
  const version = f.page().termSetVersion;
  f.add();
  assert.throws(() => f.page(1, version), changed);
  assert.deepEqual(runMigrations(f.reader.db).applied, []);
});

for (const mutation of ["insert", "update", "delete", "import", "same-count-replacement", "glossary-update"] as const) {
  test(`Select All rejects ${mutation} during retrieval without changing existing selection`, async () => {
    const f = fixture();
    const op = operation(f, async (number, _signal, version) => {
      const result = f.page(number, version);
      if (number === 1) {
        if (mutation === "insert") f.add();
        if (mutation === "glossary-update") updateGlossary(f.writer.db, "owner", f.glossary.glossaryId, "Changed glossary", "changed");
        if (mutation === "update") updateTerm(f.writer.db, "owner", "term-0", { glossaryId: f.glossary.glossaryId, sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "更新用語", targetTerm: "updated", description: null });
        if (mutation === "delete") deleteTerm(f.writer.db, "owner", "term-0");
        if (mutation === "import") importGlossaryUseCase(f.writer.db, "owner", { target: { glossaryId: f.glossary.glossaryId }, document: { terms: [{ sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "輸入用語", targetTerm: "imported", description: null }] } });
        if (mutation === "same-count-replacement") { deleteTerm(f.writer.db, "owner", "term-0"); f.add(); }
      }
      return result;
    });
    await assert.rejects(op.start(), changed);
    assert.deepEqual([...op.selected()], ["previous"]);
    assert.equal(op.commits(), 0);
  });
}

test("final check detects a write after the last page, including complete-cache paths", async () => {
  const f = fixture(101);
  const op = operation(f, async (number, _signal, version) => {
    const result = f.page(number, version);
    if (number === 2) f.add();
    return result;
  });
  await assert.rejects(op.start(), changed);
  assert.equal(op.commits(), 0);
});

test("Term UPDATE moving Glossary invalidates both sides and direct SQL writes are covered", () => {
  const f = fixture(1);
  const second = createGlossary(f.writer.db, "owner", "Other synthetic", null);
  const beforeFirst = f.page().termSetVersion;
  const beforeSecond = listVersionedTerms(f.reader.db, "owner", second.glossaryId, 1).termSetVersion;
  f.writer.db.prepare("UPDATE terms SET glossary_id = ?, description = ? WHERE term_id = ?").run(second.glossaryId, "updated", "term-0");
  assert.throws(() => f.page(1, beforeFirst), changed);
  assert.throws(() => listVersionedTerms(f.reader.db, "owner", second.glossaryId, 1, beforeSecond), changed);
});

test("failed import rolls back Term inserts and revision increments together", () => {
  const f = fixture(1);
  const version = f.page().termSetVersion;
  assert.throws(() => importGlossaryUseCase(f.writer.db, "owner", {
    target: { glossaryId: f.glossary.glossaryId }, document: { terms: [
      { sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "new", targetTerm: "new" },
      { sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "用語0", targetTerm: "term 0" },
    ] },
  }));
  assert.equal(f.page().total, 1);
  assert.equal(f.page().termSetVersion, version);
});

test("COUNT/items/version remain one read snapshot if another connection commits before COUNT", () => {
  const f = fixture(1);
  f.reader.db.exec("PRAGMA journal_mode=WAL"); // isolated test file only
  const before = f.page();
  let mutated = false;
  const proxy = new Proxy(f.reader.db, {
    get(target, property) {
      if (property === "prepare") return (sql: string) => {
        if (!mutated && sql.includes("COUNT(*)")) { mutated = true; f.add(); }
        return target.prepare(sql);
      };
      const value = Reflect.get(target, property, target) as unknown;
      return typeof value === "function" ? value.bind(target) : value;
    },
  }) as DatabaseSync;
  const during = listVersionedTerms(proxy, "owner", f.glossary.glossaryId, 1, before.termSetVersion);
  assert.equal(mutated, true);
  assert.equal(during.total, 1);
  assert.equal(during.items.length, 1);
  assert.equal(during.termSetVersion, before.termSetVersion);
  assert.equal(f.page().total, 2);
  assert.throws(() => f.request({ validateOnly: "true", expectedVersion: before.termSetVersion }), changed);
});

test("API preserves old page fields, checks expected revisions, malformed parameters, ownership and deletion", () => {
  const f = fixture(1);
  const first = f.page();
  assert.equal(first.page, 1); assert.equal(first.pageSize, 100); assert.equal(first.total, 1); assert.equal(first.hasNextPage, false);
  assert.deepEqual(f.request({ validateOnly: "true", expectedVersion: first.termSetVersion }), { termSetVersion: first.termSetVersion });
  const invalid: Array<Record<string, string>> = [{ validateOnly: "true" }, { expectedVersion: "bad" }, { validateOnly: "false" }, { page: "0" }];
  for (const params of invalid) {
    assert.throws(() => f.request(params), (error: unknown) => error instanceof AppError && error.status === 400);
  }
  assert.throws(() => f.request({}, "other"), (error: unknown) => error instanceof AppError && error.status === 404);
  assert.throws(() => f.request({ validateOnly: "true", expectedVersion: first.termSetVersion }, "other"), (error: unknown) => error instanceof AppError && error.status === 404);
  const wrongServerEpoch = `${first.termSetVersion.startsWith("0") ? "1" : "0"}${first.termSetVersion.slice(1)}`;
  assert.throws(() => f.page(1, wrongServerEpoch), changed);
  f.writer.db.prepare("DELETE FROM users WHERE user_id = ?").run("owner");
  assert.equal(f.reader.db.prepare("SELECT COUNT(*) AS count FROM glossary_term_revisions").get<{ count: number }>()?.count, 0);
  assert.throws(() => f.page(1, first.termSetVersion), (error: unknown) => error instanceof AppError && error.status === 404);
});

test("success commits once after final validation and selected-ID overlap is counted once", async () => {
  const f = fixture(201);
  let finalChecked = false;
  let selected = new Set(["term-0", "unrelated"]);
  let commits = 0;
  const result = await executeSelectAll(async (number, _signal, version) => f.page(number, version), new AbortController().signal, {
    scheduler: new TermFetchScheduler(), concurrency: 4,
    validateVersion: async (version) => { const result = await f.validate(version); finalChecked = true; return result; },
    isCurrent: () => true, getSelected: () => selected,
    commit: (next) => { assert.equal(finalChecked, true); commits++; selected = next; },
  });
  assert.equal(result.status, "complete");
  assert.equal(commits, 1); assert.equal(selected.size, 202);
});

test("re-created Glossary IDs get a new epoch and cannot reuse old cache versions", () => {
  const f = fixture(1);
  const version = f.page().termSetVersion;
  f.writer.db.prepare("DELETE FROM glossaries WHERE glossary_id = ?").run(f.glossary.glossaryId);
  f.writer.db.prepare("INSERT INTO glossaries (glossary_id, owner_user_id, name) VALUES (?, 'owner', 'Recreated')").run(f.glossary.glossaryId);
  assert.throws(() => f.page(1, version), changed);
});

test("revision counters retain 64-bit precision and reject overflow instead of reusing a version", () => {
  const f = fixture(1);
  f.writer.db.prepare("UPDATE glossary_term_revisions SET revision = ? WHERE glossary_id = ?").run("9007199254740993", f.glossary.glossaryId);
  f.add();
  assert.equal(f.page().termSetVersion.split(":").at(-1), "9007199254740994");
  f.writer.db.prepare("UPDATE glossary_term_revisions SET revision = ? WHERE glossary_id = ?").run("9223372036854775807", f.glossary.glossaryId);
  const count = f.page().total;
  assert.throws(() => createTerm(f.writer.db, "owner", { glossaryId: f.glossary.glossaryId, sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "overflow", targetTerm: "overflow", description: null }), /CHECK constraint/);
  assert.equal(f.page().total, count);
});

test("changed revisions cannot reuse a complete old cache", async () => {
  const f = fixture(201);
  const first = f.page();
  const cached = new Map([[1, first], [2, f.page(2, first.termSetVersion)], [3, f.page(3, first.termSetVersion)]]);
  const changedId = cached.get(2)!.items[0].termId;
  f.writer.db.prepare("UPDATE terms SET description = ? WHERE term_id = ?").run("Changed description", changedId);
  const calls: number[] = [];
  let selected = new Set<string>();
  const result = await executeSelectAll(async (number, _signal, version) => { calls.push(number); return f.page(number, version); }, new AbortController().signal, {
    scheduler: new TermFetchScheduler(), cachedPages: cached, validateVersion: f.validate,
    isCurrent: () => true, getSelected: () => selected, commit: (next) => { selected = next; },
  });
  assert.equal(result.status, "complete");
  assert.deepEqual(calls.sort(), [1, 2, 3]);
  assert.equal(selected.size, 201);
  if (result.status === "complete") {
    assert.equal(result.pages.flatMap((p) => p.items).find((term) => term.termId === changedId)?.description, "Changed description");
  }
});
