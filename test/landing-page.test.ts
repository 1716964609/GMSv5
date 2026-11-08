import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, it } from "node:test";

import { openDatabase } from "../src/lib/db/sqlite.ts";
import { runMigrations } from "../src/lib/db/migrations.ts";
import { importGlossaryUseCase } from "../src/lib/services/glossary.ts";
import { listGlossaries, listTerms } from "../src/lib/db/repository.ts";

const root = process.cwd();
const page = readFileSync(join(root, "src/app/page.tsx"), "utf8");
const visual = readFileSync(join(root, "src/app/landing-visual.tsx"), "utf8");
const styles = readFileSync(join(root, "src/app/page.module.css"), "utf8");
const requirements = readFileSync(join(root, "docs/requirements.md"), "utf8");

describe("public landing page contract", () => {
  it("connects all header anchors to matching sections", () => {
    for (const id of ["features", "how-it-works", "privacy", "faq"]) {
      assert.match(page, new RegExp(`"#[ ]?${id}"`));
      assert.match(page, new RegExp(`id="${id}"`));
    }
    assert.match(styles, /scroll-margin-top:\s*96px/);
    assert.match(styles, /position:\s*sticky/);
  });

  it("uses existing login and registration routes for every CTA", () => {
    assert.equal((page.match(/href="\/register"/g) ?? []).length, 3);
    assert.equal((page.match(/href="\/login"/g) ?? []).length, 3);
  });

  it("provides keyboard-operable native FAQ disclosures", () => {
    assert.match(page, /<details className=\{styles\.faqItem\}/);
    assert.match(page, /<summary>\{question\}<\/summary>/);
    assert.match(styles, /\.faqItem summary:focus-visible/);
  });

  it("keeps motion optional and supports reduced-motion preferences", () => {
    assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(styles, /animation:\s*none/);
  });

  it("keeps approved pricing, contact, and honest concept labeling", () => {
    assert.match(page, /GMSは無料でご利用いただけます/);
    assert.match(page, /mailto:HXLJ9909@Outlook\.jp/);
    assert.match(requirements, /コンセプトビジュアル/);
    assert.match(visual, /<small>CONCEPT<\/small>/);
    assert.doesNotMatch(visual, /<figcaption|実際の製品画面ではありません/);
    assert.match(visual, /aria-hidden="true"/);
    assert.doesNotMatch(visual, /<(button|input|select|a)\b/);
    assert.match(requirements, /未実行の翻訳を実行済みとして示さない/);
  });

  it("provides three isolated JSON-importable synthetic medical glossaries", () => {
    const fixtures = [
      ["clinical-trials.json", "臨床試験・治験用語集"],
      ["pharmacovigilance.json", "医薬品安全性・有害事象用語集"],
      ["labeling-pharmacology.json", "医薬品添付文書・薬理用語集"],
    ];
    const directory = mkdtempSync(join(tmpdir(), "gms-landing-import-test-"));
    const handle = openDatabase(join(directory, "isolated.sqlite"), "test");
    try {
      runMigrations(handle.db);
      handle.db.prepare(
        `INSERT INTO users (user_id, email, auth_subject, account_status)
         VALUES (?, ?, ?, 'ACTIVE')`,
      ).run("landing-demo-user", "landing-demo@example.test", "landing-demo-subject");

      for (const [fixture, glossaryName] of fixtures) {
        const document = JSON.parse(
          readFileSync(join(root, "scripts/data/landing-demo", fixture), "utf8"),
        ) as { terms?: Array<Record<string, unknown>> };
        assert.ok(Array.isArray(document.terms));
        assert.ok(document.terms.length > 0);
        for (const term of document.terms) {
          assert.deepEqual(Object.keys(term).sort(), [
            "description",
            "sourceLanguage",
            "sourceTerm",
            "targetLanguage",
            "targetTerm",
          ]);
          assert.equal(term.sourceLanguage, "JA");
          assert.equal(term.targetLanguage, "EN");
        }

        const imported = importGlossaryUseCase(handle.db, "landing-demo-user", {
          target: { name: glossaryName },
          document,
        });
        assert.equal(imported.inserted, document.terms.length);
      }

      const glossaryPage = listGlossaries(handle.db, "landing-demo-user", 1);
      assert.deepEqual(
        glossaryPage.items.map(({ name }) => name).sort(),
        fixtures.map(([, name]) => name).sort(),
      );
      for (const glossary of glossaryPage.items) {
        const terms = listTerms(handle.db, "landing-demo-user", glossary.glossaryId, 1);
        assert.ok(terms.items.length > 0);
        assert.equal(terms.items.every((term) => term.sourceLanguage === "JA" && term.targetLanguage === "EN"), true);
      }
    } finally {
      handle.db.close();
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("keeps Hero headline phrases together on wide layouts and wrappable on mobile", () => {
    assert.match(page, /<span className=\{styles\.heroTitleLine\}>専門用語の翻訳を、<\/span>/);
    assert.match(page, /<span className=\{styles\.heroTitleLine\}>もっと思いどおりに。<\/span>/);
    assert.match(styles, /\.heroTitleLine\s*\{\s*display:\s*block;\s*white-space:\s*nowrap;/);
    assert.match(styles, /\.heroTitleLine\s*\{\s*white-space:\s*normal;/);
  });
});
