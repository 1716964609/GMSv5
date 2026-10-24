import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "node:test";

import { AppError } from "../src/lib/domain/errors.ts";
import {
  createGlossary,
  createTerm,
  findUserById,
  getCredentialSecret,
  listCredentialSummaries,
  listGlossaries,
  listTerms,
  purgeEligibleAccounts,
  requestAccountDeletion,
  searchTerms,
  upsertCredential,
} from "../src/lib/db/repository.ts";
import { runMigrations } from "../src/lib/db/migrations.ts";
import { openDatabase, type DatabaseHandle } from "../src/lib/db/sqlite.ts";
import { encryptSecret, decryptSecret } from "../src/lib/security/credentials.ts";
import { importGlossaryUseCase } from "../src/lib/services/glossary.ts";
import {
  translateUseCase,
} from "../src/lib/services/translation.ts";
import {
  deepLProvider,
  openAIProvider,
  ProviderError,
} from "../src/lib/translation/providers.ts";

const handles: DatabaseHandle[] = [];
const previousKey = process.env.GMS_CREDENTIAL_ENCRYPTION_KEY;
const previousDeepLUrl = process.env.DEEPL_API_URL;
const previousOpenAIUrl = process.env.OPENAI_API_URL;
const originalFetch = globalThis.fetch;

afterEach(() => {
  for (const handle of handles.splice(0)) {
    handle.db.close();
    rmSync(handle.path, { force: true });
    rmSync(join(handle.path, ".."), { recursive: true, force: true });
  }
  if (previousKey === undefined) delete process.env.GMS_CREDENTIAL_ENCRYPTION_KEY;
  else process.env.GMS_CREDENTIAL_ENCRYPTION_KEY = previousKey;
  if (previousDeepLUrl === undefined) delete process.env.DEEPL_API_URL;
  else process.env.DEEPL_API_URL = previousDeepLUrl;
  if (previousOpenAIUrl === undefined) delete process.env.OPENAI_API_URL;
  else process.env.OPENAI_API_URL = previousOpenAIUrl;
  globalThis.fetch = originalFetch;
});

function database() {
  const directory = mkdtempSync(join(tmpdir(), "gmsv5-browserless-test-"));
  const handle = openDatabase(join(directory, "gmsv5.sqlite"), "test");
  runMigrations(handle.db);
  handles.push(handle);
  process.env.GMS_CREDENTIAL_ENCRYPTION_KEY = randomBytes(32).toString("base64");
  return handle.db;
}

function user(db: ReturnType<typeof database>, userId: string): void {
  db.prepare(
    `INSERT INTO users (user_id, email, auth_subject, account_status)
     VALUES (?, ?, ?, 'ACTIVE')`,
  ).run(userId, `${userId}@example.test`, `subject-${userId}`);
}

describe("GMS v5 browserless acceptance coverage", () => {
  it("enforces 100-item pagination and owner-scoped search", () => {
    const db = database();
    user(db, "owner");
    user(db, "other");

    for (let index = 0; index < 101; index += 1) {
      createGlossary(db, "owner", `Glossary ${index}`, null);
    }
    const glossaryPage1 = listGlossaries(db, "owner", 1);
    const glossaryPage2 = listGlossaries(db, "owner", 2);
    assert.equal(glossaryPage1.items.length, 100);
    assert.equal(glossaryPage1.total, 101);
    assert.equal(glossaryPage1.hasNextPage, true);
    assert.equal(glossaryPage2.items.length, 1);
    assert.equal(glossaryPage2.hasNextPage, false);

    const glossary = createGlossary(db, "owner", "Terms", null);
    for (let index = 0; index < 105; index += 1) {
      createTerm(db, "owner", {
        glossaryId: glossary.glossaryId,
        sourceLanguage: "JA",
        targetLanguage: "EN",
        sourceTerm: `source-${index}`,
        targetTerm: `target-${index}`,
        description: null,
      });
    }
    const termPage1 = listTerms(db, "owner", glossary.glossaryId, 1);
    const termPage2 = listTerms(db, "owner", glossary.glossaryId, 2);
    assert.equal(termPage1.items.length, 100);
    assert.equal(termPage1.total, 105);
    assert.equal(termPage1.hasNextPage, true);
    assert.equal(termPage2.items.length, 5);
    assert.equal(termPage2.hasNextPage, false);

    const otherGlossary = createGlossary(db, "other", "Other", null);
    createTerm(db, "other", {
      glossaryId: otherGlossary.glossaryId,
      sourceLanguage: "JA",
      targetLanguage: "EN",
      sourceTerm: "owner-secret",
      targetTerm: "private",
      description: null,
    });
    assert.equal(searchTerms(db, "owner", "private").length, 0);
    assert.equal(searchTerms(db, "owner", "source-104").length, 1);
  });

  it("rejects duplicate import rows before changing the target glossary", () => {
    const db = database();
    user(db, "owner");
    const glossary = createGlossary(db, "owner", "Existing", null);

    assert.throws(
      () =>
        importGlossaryUseCase(db, "owner", {
          target: { glossaryId: glossary.glossaryId },
          document: {
            terms: [
              { sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "心不全", targetTerm: "failure" },
              { sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: " 心不全 ", targetTerm: "failure" },
            ],
          },
        }),
      (error: unknown) => error instanceof AppError && error.code === "DUPLICATE_TERM",
    );
    assert.equal(listTerms(db, "owner", glossary.glossaryId, 1).total, 0);
  });

  it("does not call providers without consent and stops OpenAI after DeepL failure", async () => {
    const db = database();
    user(db, "owner");
    upsertCredential(db, "owner", "DEEPL", encryptSecret("deepl-secret"));
    upsertCredential(db, "owner", "OPENAI", encryptSecret("openai-secret"));
    let deepLCalls = 0;
    let openAICalls = 0;

    await assert.rejects(
      () =>
        translateUseCase(
          db,
          "owner",
          {
            originalText: "本文",
            sourceLanguage: "JA",
            targetLanguage: "EN",
            selectedTermIds: [],
            consentConfirmed: false,
          },
          {
            deepL: { translate: async () => { deepLCalls += 1; return "一次"; } },
            openAI: { translate: async () => { openAICalls += 1; return "最終"; } },
          },
        ),
      (error: unknown) => error instanceof AppError && error.code === "TRANSLATION_CONSENT_REQUIRED",
    );
    assert.equal(deepLCalls, 0);
    assert.equal(openAICalls, 0);

    await assert.rejects(
      () =>
        translateUseCase(
          db,
          "owner",
          {
            originalText: "本文",
            sourceLanguage: "JA",
            targetLanguage: "EN",
            selectedTermIds: [],
            consentConfirmed: true,
          },
          {
            deepL: {
              translate: async () => {
                deepLCalls += 1;
                throw new ProviderError("DEEPL", "controlled DeepL failure");
              },
            },
            openAI: { translate: async () => { openAICalls += 1; return "最終"; } },
          },
        ),
      (error: unknown) => error instanceof ProviderError && error.provider === "DEEPL",
    );
    assert.equal(deepLCalls, 1);
    assert.equal(openAICalls, 0);
  });

  it("stores encrypted credentials, deletes them immediately, and purges owned data after 30 days", () => {
    const db = database();
    user(db, "owner");
    const glossary = createGlossary(db, "owner", "Owned", null);
    createTerm(db, "owner", {
      glossaryId: glossary.glossaryId,
      sourceLanguage: "JA",
      targetLanguage: "EN",
      sourceTerm: "source",
      targetTerm: "target",
      description: null,
    });
    const encrypted = encryptSecret("plaintext-api-key");
    upsertCredential(db, "owner", "DEEPL", encrypted);
    assert.notEqual(getCredentialSecret(db, "owner", "DEEPL"), "plaintext-api-key");
    assert.equal(decryptSecret(getCredentialSecret(db, "owner", "DEEPL")!), "plaintext-api-key");
    assert.deepEqual(listCredentialSummaries(db, "owner").map(({ provider }) => provider), ["DEEPL"]);

    const pending = requestAccountDeletion(db, "owner");
    assert.equal(pending.accountStatus, "PENDING_DELETION");
    assert.equal(getCredentialSecret(db, "owner", "DEEPL"), null);
    assert.equal(requestAccountDeletion(db, "owner").accountStatus, "PENDING_DELETION");

    db.prepare("UPDATE users SET deletion_scheduled_at = ? WHERE user_id = ?")
      .run("2000-01-01T00:00:00.000Z", "owner");
    assert.deepEqual(purgeEligibleAccounts(db).map(({ userId }) => userId), ["owner"]);
    assert.equal(findUserById(db, "owner"), null);
    assert.equal(db.prepare("SELECT COUNT(*) AS count FROM glossaries").get<{ count: number }>()?.count, 0);
    assert.equal(purgeEligibleAccounts(db).length, 0);
  });

  it("uses the configured DeepL and OpenAI HTTP contracts through a local mock", async () => {
    process.env.DEEPL_API_URL = "https://mock.deepl.test/v2/translate";
    process.env.OPENAI_API_URL = "https://mock.openai.test/v1/chat/completions";
    const calls: Array<{ url: string; method: string; body: string }> = [];
    globalThis.fetch = async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string"
        ? init.body
        : init?.body instanceof URLSearchParams
          ? init.body.toString()
          : "";
      calls.push({ url, method, body });
      if (url.endsWith("/glossaries") && method === "POST") {
        return new Response(JSON.stringify({ glossary_id: "temporary-glossary" }), { status: 200 });
      }
      if (url.endsWith("/translate") && method === "POST") {
        return new Response(JSON.stringify({ translations: [{ text: "一次訳" }] }), { status: 200 });
      }
      if (url.includes("mock.deepl.test") && method === "DELETE") {
        return new Response("", { status: 204 });
      }
      if (url.includes("mock.openai.test") && method === "POST") {
        return new Response(
          JSON.stringify({ choices: [{ message: { content: "最終訳" }, finish_reason: "stop" }] }),
          { status: 200 },
        );
      }
      return new Response("not found", { status: 404 });
    };

    const first = await deepLProvider.translate(
      {
        originalText: "本文",
        sourceLanguage: "JA",
        targetLanguage: "EN",
        terms: [{ sourceTerm: "心不全", targetTerm: "heart failure" }],
      },
      "deepl-secret",
    );
    const final = await openAIProvider.translate(
      {
        originalText: "本文",
        initialTranslation: first,
        sourceLanguage: "JA",
        targetLanguage: "EN",
        terms: [{ sourceTerm: "心不全", targetTerm: "heart failure" }],
      },
      "openai-secret",
    );

    assert.equal(first, "一次訳");
    assert.equal(final, "最終訳");
    assert.deepEqual(calls.map(({ method, url }) => `${method} ${url}`), [
      "POST https://mock.deepl.test/v2/glossaries",
      "POST https://mock.deepl.test/v2/translate",
      "DELETE https://mock.deepl.test/v2/glossaries/temporary-glossary",
      "POST https://mock.openai.test/v1/chat/completions",
    ]);
    const openAIBody = JSON.parse(calls[3].body) as { model: string; reasoning_effort: string };
    assert.equal(openAIBody.model, "gpt-6-luna");
    assert.equal(openAIBody.reasoning_effort, "medium");
  });
});
