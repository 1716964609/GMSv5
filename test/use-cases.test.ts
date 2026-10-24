import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "node:test";

import { runMigrations } from "../src/lib/db/migrations.ts";
import {
  createGlossary,
  createTerm,
  findUserById,
  getCredentialSecret,
  importTermsTransaction,
  listTerms,
  purgeEligibleAccounts,
  requestAccountDeletion,
  upsertCredential,
} from "../src/lib/db/repository.ts";
import { encryptSecret, decryptSecret } from "../src/lib/security/credentials.ts";
import { importGlossaryUseCase } from "../src/lib/services/glossary.ts";
import { translateUseCase, TranslationConflictError } from "../src/lib/services/translation.ts";
import { openDatabase, type DatabaseHandle } from "../src/lib/db/sqlite.ts";

const handles: DatabaseHandle[] = [];
const previousKey = process.env.GMS_CREDENTIAL_ENCRYPTION_KEY;

afterEach(() => {
  for (const handle of handles.splice(0)) {
    handle.db.close();
    rmSync(handle.path, { force: true });
    rmSync(join(handle.path, ".."), { recursive: true, force: true });
  }
  if (previousKey === undefined) delete process.env.GMS_CREDENTIAL_ENCRYPTION_KEY;
  else process.env.GMS_CREDENTIAL_ENCRYPTION_KEY = previousKey;
});

function database() {
  const directory = mkdtempSync(join(tmpdir(), "gmsv5-use-case-test-"));
  const handle = openDatabase(join(directory, "gmsv5.sqlite"), "test");
  runMigrations(handle.db);
  handles.push(handle);
  process.env.GMS_CREDENTIAL_ENCRYPTION_KEY = randomBytes(32).toString("base64");
  return handle.db;
}

function user(db: ReturnType<typeof database>, userId: string, email = `${userId}@example.test`) {
  db.prepare(
    `INSERT INTO users (user_id, email, auth_subject, account_status)
     VALUES (?, ?, ?, 'ACTIVE')`,
  ).run(userId, email, `subject-${userId}`);
}

describe("GMS v5 Use Case services", () => {
  it("keeps glossary and term access inside the authenticated owner's boundary", () => {
    const db = database();
    user(db, "owner");
    user(db, "other");
    const glossary = createGlossary(db, "owner", "Owner glossary", null);
    createTerm(db, "owner", {
      glossaryId: glossary.glossaryId,
      sourceLanguage: "JA",
      targetLanguage: "EN",
      sourceTerm: " 心不全 ",
      targetTerm: "cardiac failure",
      description: null,
    });
    createTerm(db, "owner", {
      glossaryId: glossary.glossaryId,
      sourceLanguage: "JA",
      targetLanguage: "EN",
      sourceTerm: "心不全",
      targetTerm: "heart failure",
      description: null,
    });

    assert.equal(listTerms(db, "owner", glossary.glossaryId, 1).total, 2);
    assert.throws(() => listTerms(db, "other", glossary.glossaryId, 1), /Glossary was not found/);
    assert.throws(
      () =>
        createTerm(db, "owner", {
          glossaryId: glossary.glossaryId,
          sourceLanguage: "JA",
          targetLanguage: "EN",
          sourceTerm: "心不全",
          targetTerm: " cardiac failure ",
          description: null,
        }),
      /UNIQUE constraint failed/,
    );
  });

  it("validates JSON imports before committing and rolls back an existing conflict", () => {
    const db = database();
    user(db, "owner");
    const glossary = createGlossary(db, "owner", "Existing", null);
    createTerm(db, "owner", {
      glossaryId: glossary.glossaryId,
      sourceLanguage: "EN",
      targetLanguage: "JA",
      sourceTerm: "term",
      targetTerm: "用語",
      description: null,
    });

    assert.throws(
      () =>
        importGlossaryUseCase(db, "owner", {
          target: { glossaryId: glossary.glossaryId },
          document: {
            terms: [
              { sourceLanguage: "EN", targetLanguage: "JA", sourceTerm: "term", targetTerm: "用語" },
            ],
          },
        }),
      /conflicts with an existing term/,
    );
    assert.equal(listTerms(db, "owner", glossary.glossaryId, 1).total, 1);

    assert.throws(
      () =>
        importGlossaryUseCase(db, "owner", {
          target: { name: "Invalid" },
          document: {
            terms: [
              { sourceLanguage: "EN", targetLanguage: "JA", sourceTerm: "a", targetTerm: "b", extra: true },
            ],
          },
        }),
      /unknown fields/,
    );
    const glossaryCount = db.prepare("SELECT COUNT(*) AS count FROM glossaries").get<{ count: number }>();
    assert.equal(glossaryCount?.count, 1);
  });

  it("stops before external APIs for a conflict and otherwise runs DeepL then OpenAI", async () => {
    const db = database();
    user(db, "owner");
    const first = createGlossary(db, "owner", "First", null);
    const second = createGlossary(db, "owner", "Second", null);
    const firstTerm = createTerm(db, "owner", { glossaryId: first.glossaryId, sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "心不全", targetTerm: "cardiac failure", description: null });
    const secondTerm = createTerm(db, "owner", { glossaryId: second.glossaryId, sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "心不全", targetTerm: "heart failure", description: null });
    upsertCredential(db, "owner", "DEEPL", encryptSecret("deepl-secret"));
    upsertCredential(db, "owner", "OPENAI", encryptSecret("openai-secret"));
    assert.equal(decryptSecret(getCredentialSecret(db, "owner", "DEEPL")!), "deepl-secret");

    const calls: string[] = [];
    const deepL = { translate: async (input: { terms: unknown[] }) => { calls.push(`deepl:${input.terms.length}`); return "一次訳"; } };
    const openAI = { translate: async (input: { initialTranslation?: string }) => { calls.push(`openai:${input.initialTranslation}`); return "最終訳"; } };
    await assert.rejects(
      () => translateUseCase(db, "owner", { originalText: "本文", sourceLanguage: "JA", targetLanguage: "EN", selectedTermIds: [firstTerm.termId, secondTerm.termId], consentConfirmed: true }, { deepL, openAI }),
      (error: unknown) => error instanceof TranslationConflictError,
    );
    assert.deepEqual(calls, []);

    const result = await translateUseCase(db, "owner", { originalText: "本文", sourceLanguage: "JA", targetLanguage: "EN", selectedTermIds: [firstTerm.termId, secondTerm.termId], consentConfirmed: true, resolutions: { 心不全: secondTerm.termId } }, { deepL, openAI });
    assert.equal(result.translatedText, "最終訳");
    assert.deepEqual(calls, ["deepl:1", "openai:一次訳"]);
  });

  it("supports zero selected terms without persisting translation text", async () => {
    const db = database();
    user(db, "owner");
    upsertCredential(db, "owner", "DEEPL", encryptSecret("deepl-secret"));
    upsertCredential(db, "owner", "OPENAI", encryptSecret("openai-secret"));
    const result = await translateUseCase(db, "owner", { originalText: "本文", sourceLanguage: "EN", targetLanguage: "JA", selectedTermIds: [], consentConfirmed: true }, { deepL: { translate: async () => "一次" }, openAI: { translate: async () => "最終" } });
    assert.equal(result.translatedText, "最終");
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all<{ name: string }>().map((row) => row.name);
    assert.equal(tables.includes("translation_results"), false);
  });

  it("deletes credentials immediately and purges pending accounts idempotently", () => {
    const db = database();
    user(db, "owner");
    upsertCredential(db, "owner", "DEEPL", encryptSecret("secret"));
    const pending = requestAccountDeletion(db, "owner");
    assert.equal(pending.accountStatus, "PENDING_DELETION");
    assert.equal(getCredentialSecret(db, "owner", "DEEPL"), null);
    assert.equal(pending.deletionScheduledAt !== null, true);
    db.prepare("UPDATE users SET deletion_scheduled_at = ? WHERE user_id = ?").run("2000-01-01T00:00:00.000Z", "owner");
    assert.equal(purgeEligibleAccounts(db).length, 1);
    assert.equal(purgeEligibleAccounts(db).length, 0);
    assert.equal(findUserById(db, "owner"), null);
  });

  it("can execute a transaction for a new glossary and terms", () => {
    const db = database();
    user(db, "owner");
    const result = importTermsTransaction(db, "owner", { glossaryName: "Imported", terms: [{ sourceLanguage: "EN", targetLanguage: "JA", sourceTerm: "hello", targetTerm: "こんにちは", description: null }] });
    assert.equal(result.inserted, 1);
    assert.equal(listTerms(db, "owner", result.glossaryId, 1).total, 1);
  });
});
