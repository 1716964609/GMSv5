import type { DatabaseSync } from "node:sqlite";

import {
  isUniqueConstraintError,
  AppError,
} from "../domain/errors.ts";
import type { Glossary, Term } from "../domain/types.ts";
import {
  createGlossary,
  createTerm,
  deleteGlossary,
  deleteTerm,
  findGlossaryForOwner,
  findTermForOwner,
  importTermsTransaction,
  listGlossaries,
  listTerms,
  searchTerms,
  updateGlossary,
  updateTerm,
} from "../db/repository.ts";
import { normalizeTermPair } from "../db/normalization.ts";
import {
  assertNoUnknownKeys,
  asRecord,
  MAX_IMPORT_TERMS,
  optionalString,
  requiredString,
} from "../validation.ts";

export type TermInput = {
  glossaryId: string;
  sourceLanguage: string;
  targetLanguage: string;
  sourceTerm: string;
  targetTerm: string;
  description: string | null;
};

function termInput(value: unknown, context: string): Omit<TermInput, "glossaryId"> {
  const record = asRecord(value, context);
  assertNoUnknownKeys(
    record,
    ["sourceLanguage", "targetLanguage", "sourceTerm", "targetTerm", "description"],
    context,
  );
  return {
    sourceLanguage: requiredString(record.sourceLanguage, `${context}.sourceLanguage`, {
      maxLength: 50,
    }),
    targetLanguage: requiredString(record.targetLanguage, `${context}.targetLanguage`, {
      maxLength: 50,
    }),
    sourceTerm: requiredString(record.sourceTerm, `${context}.sourceTerm`, {
      maxLength: 10_000,
    }),
    targetTerm: requiredString(record.targetTerm, `${context}.targetTerm`, {
      maxLength: 10_000,
    }),
    description: optionalString(record.description, `${context}.description`, {
      maxLength: 10_000,
    }),
  };
}

export function parseTermInput(value: unknown, glossaryId: string): TermInput {
  return { glossaryId, ...termInput(value, "term") };
}

export function createGlossaryUseCase(
  db: DatabaseSync,
  userId: string,
  value: unknown,
): Glossary {
  const record = asRecord(value, "glossary");
  assertNoUnknownKeys(record, ["name", "description"], "glossary");
  return createGlossary(
    db,
    userId,
    requiredString(record.name, "name", { maxLength: 200 }),
    optionalString(record.description, "description", { maxLength: 10_000 }),
  );
}

export function updateGlossaryUseCase(
  db: DatabaseSync,
  userId: string,
  value: unknown,
  glossaryId: string,
): Glossary {
  const record = asRecord(value, "glossary");
  assertNoUnknownKeys(record, ["name", "description"], "glossary");
  return updateGlossary(
    db,
    userId,
    glossaryId,
    requiredString(record.name, "name", { maxLength: 200 }),
    optionalString(record.description, "description", { maxLength: 10_000 }),
  );
}

export function createTermUseCase(
  db: DatabaseSync,
  userId: string,
  value: unknown,
  glossaryId: string,
): Term {
  try {
    return createTerm(db, userId, parseTermInput(value, glossaryId));
  } catch (error) {
    if (isUniqueConstraintError(error)) {
      throw new AppError(
        409,
        "DUPLICATE_TERM",
        "The normalized four Term attributes already exist in this glossary",
      );
    }
    throw error;
  }
}

export function updateTermUseCase(
  db: DatabaseSync,
  userId: string,
  value: unknown,
  termId: string,
): Term {
  const existing = findTermForOwner(db, userId, termId);
  if (!existing) {
    throw new AppError(404, "TERM_NOT_FOUND", "Term was not found");
  }
  try {
    return updateTerm(
      db,
      userId,
      termId,
      parseTermInput(value, existing.glossaryId),
    );
  } catch (error) {
    if (isUniqueConstraintError(error)) {
      throw new AppError(
        409,
        "DUPLICATE_TERM",
        "The normalized four Term attributes already exist in this glossary",
      );
    }
    throw error;
  }
}

export function importGlossaryUseCase(
  db: DatabaseSync,
  userId: string,
  input: {
    target: { glossaryId?: string; name?: string; description?: string | null };
    document: unknown;
  },
): { glossaryId: string; inserted: number } {
  const document = asRecord(input.document, "JSON import");
  assertNoUnknownKeys(document, ["terms"], "JSON import");
  if (!Array.isArray(document.terms)) {
    throw new AppError(400, "INVALID_IMPORT", "JSON import terms must be an array");
  }
  if (document.terms.length > MAX_IMPORT_TERMS) {
    throw new AppError(400, "IMPORT_LIMIT_EXCEEDED", "JSON import is limited to 10,000 terms");
  }

  const terms = document.terms.map((value, index) => {
    try {
      return termInput(value, `terms[${index}]`);
    } catch (error) {
      if (error instanceof AppError) {
        throw new AppError(error.status, error.code, error.message, {
          ...(error.details ?? {}),
          index,
        });
      }
      throw error;
    }
  });

  const seen = new Set<string>();
  for (const [index, term] of terms.entries()) {
    const normalized = normalizeTermPair(term.sourceTerm, term.targetTerm);
    const key = [
      term.sourceLanguage,
      term.targetLanguage,
      normalized.sourceTerm,
      normalized.targetTerm,
    ].join("\u0000");
    if (seen.has(key)) {
      throw new AppError(409, "DUPLICATE_TERM", "JSON import contains a duplicate term", {
        index,
      });
    }
    seen.add(key);
  }

  const target = input.target;
  if (target.glossaryId && target.name) {
    throw new AppError(400, "INVALID_IMPORT_TARGET", "Choose either an existing glossary or a new glossary");
  }
  if (!target.glossaryId && !target.name) {
    throw new AppError(400, "INVALID_IMPORT_TARGET", "An import target is required");
  }
  const glossaryDescription = target.description ?? null;
  if (target.glossaryId && !findGlossaryForOwner(db, userId, target.glossaryId)) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }

  try {
    return importTermsTransaction(db, userId, {
      glossaryId: target.glossaryId,
      glossaryName: target.name,
      glossaryDescription,
      terms,
    });
  } catch (error) {
    if (isUniqueConstraintError(error)) {
      throw new AppError(409, "DUPLICATE_TERM", "JSON import conflicts with an existing term");
    }
    throw error;
  }
}

export function validateImportTarget(value: unknown): {
  glossaryId?: string;
  name?: string;
  description?: string | null;
} {
  const record = asRecord(value, "target");
  assertNoUnknownKeys(record, ["glossaryId", "name", "description"], "target");
  return {
    glossaryId:
      record.glossaryId === undefined
        ? undefined
        : requiredString(record.glossaryId, "target.glossaryId"),
    name:
      record.name === undefined
        ? undefined
        : requiredString(record.name, "target.name", { maxLength: 200 }),
    description: optionalString(record.description, "target.description", {
      maxLength: 10_000,
    }),
  };
}

export const glossaryQueries = {
  list: listGlossaries,
  terms: listTerms,
  search: searchTerms,
  delete: deleteGlossary,
  deleteTerm,
};
