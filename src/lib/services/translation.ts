import type { DatabaseSync } from "node:sqlite";

import {
  findTranslationTerms,
  getCredentialSecret,
} from "../db/repository.ts";
import { decryptSecret } from "../security/credentials.ts";
import {
  AppError,
} from "../domain/errors.ts";
import type {
  ConflictCandidate,
  TranslationConflict,
  TranslationTerm,
} from "../domain/types.ts";
import {
  deepLProvider,
  openAIProvider,
  type TranslationProvider,
} from "../translation/providers.ts";
import {
  MAX_TRANSLATION_TERMS,
  requiredString,
} from "../validation.ts";

export type TranslationRequestInput = {
  originalText: string;
  sourceLanguage: string;
  targetLanguage: string;
  selectedTermIds: string[];
  consentConfirmed: boolean;
  resolutions?: Record<string, string>;
};

export type TranslationResult = {
  translatedText: string;
  executionStatus: "SUCCESS";
};

export class TranslationConflictError extends AppError {
  readonly conflicts: TranslationConflict[];

  constructor(conflicts: TranslationConflict[]) {
    super(
      409,
      "TERM_CONFLICT",
      "Choose one translation candidate for each conflicting source term before translating",
      { conflicts },
    );
    this.name = "TranslationConflictError";
    this.conflicts = conflicts;
  }
}

function selectedTerms(
  db: DatabaseSync,
  userId: string,
  input: TranslationRequestInput,
): TranslationTerm[] {
  if (!Array.isArray(input.selectedTermIds) || input.selectedTermIds.some((id) => typeof id !== "string")) {
    throw new AppError(400, "INVALID_TERM_SELECTION", "selectedTermIds must be an array of IDs");
  }
  if (input.selectedTermIds.length > MAX_TRANSLATION_TERMS) {
    throw new AppError(400, "TRANSLATION_LIMIT_EXCEEDED", "A translation request may select at most 10,000 terms");
  }
  const uniqueIds = new Set(input.selectedTermIds);
  if (uniqueIds.size !== input.selectedTermIds.length) {
    throw new AppError(400, "INVALID_TERM_SELECTION", "selectedTermIds must not contain duplicates");
  }
  const terms = findTranslationTerms(db, userId, input.selectedTermIds);
  if (terms.length !== input.selectedTermIds.length) {
    throw new AppError(403, "TERM_ACCESS_DENIED", "One or more selected terms are not owned by the user");
  }
  return terms;
}

function conflictsFor(
  terms: TranslationTerm[],
  sourceLanguage: string,
  targetLanguage: string,
): TranslationConflict[] {
  const grouped = new Map<string, TranslationTerm[]>();
  for (const term of terms) {
    if (term.sourceLanguage !== sourceLanguage || term.targetLanguage !== targetLanguage) {
      continue;
    }
    const key = term.sourceTerm;
    const group = grouped.get(key) ?? [];
    group.push(term);
    grouped.set(key, group);
  }

  return [...grouped.entries()]
    .map(([sourceTerm, group]) => {
      const distinctTargets = new Map<string, TranslationTerm>();
      for (const term of group) {
        distinctTargets.set(term.targetTerm, term);
      }
      if (distinctTargets.size < 2) {
        return null;
      }
      const candidates: ConflictCandidate[] = [...distinctTargets.values()].map((term) => ({
        termId: term.termId,
        glossaryId: term.glossaryId,
        glossaryName: term.glossaryName,
        sourceTerm: term.sourceTerm,
        targetTerm: term.targetTerm,
      }));
      return {
        sourceLanguage,
        targetLanguage,
        sourceTerm,
        candidates,
      };
    })
    .filter((conflict): conflict is TranslationConflict => conflict !== null);
}

function effectiveTerms(
  terms: TranslationTerm[],
  input: TranslationRequestInput,
): TranslationTerm[] {
  const conflicts = conflictsFor(terms, input.sourceLanguage, input.targetLanguage);
  if (conflicts.length === 0) {
    return terms.filter(
      (term) =>
        term.sourceLanguage === input.sourceLanguage &&
        term.targetLanguage === input.targetLanguage,
    );
  }
  const resolutions = input.resolutions ?? {};
  const selected = new Map<string, string>();
  for (const conflict of conflicts) {
    const selectedId = resolutions[conflict.sourceTerm];
    const candidate = conflict.candidates.find((item) => item.termId === selectedId);
    if (!candidate) {
      throw new TranslationConflictError(conflicts);
    }
    selected.set(conflict.sourceTerm, candidate.termId);
  }

  return terms.filter((term) => {
    if (term.sourceLanguage !== input.sourceLanguage || term.targetLanguage !== input.targetLanguage) {
      return false;
    }
    const selectedId = selected.get(term.sourceTerm);
    if (!selectedId) {
      return true;
    }
    return term.termId === selectedId;
  });
}

function providerSecret(db: DatabaseSync, userId: string, provider: "DEEPL" | "OPENAI"): string {
  const encrypted = getCredentialSecret(db, userId, provider);
  if (!encrypted) {
    throw new AppError(400, "API_CREDENTIAL_MISSING", `${provider} API credential is required`);
  }
  return decryptSecret(encrypted);
}

export async function translateUseCase(
  db: DatabaseSync,
  userId: string,
  input: TranslationRequestInput,
  providers: {
    deepL?: TranslationProvider;
    openAI?: TranslationProvider;
  } = {},
): Promise<TranslationResult> {
  const originalText = requiredString(input.originalText, "originalText", {
    maxLength: 2_000_000,
  });
  const sourceLanguage = requiredString(input.sourceLanguage, "sourceLanguage", {
    maxLength: 50,
  });
  const targetLanguage = requiredString(input.targetLanguage, "targetLanguage", {
    maxLength: 50,
  });
  if (input.consentConfirmed !== true) {
    throw new AppError(400, "TRANSLATION_CONSENT_REQUIRED", "External data transmission consent is required");
  }

  const normalizedInput = {
    ...input,
    originalText,
    sourceLanguage,
    targetLanguage,
  };
  const terms = selectedTerms(db, userId, normalizedInput);
  const effective = effectiveTerms(terms, normalizedInput);
  const deepLKey = providerSecret(db, userId, "DEEPL");
  const openAIKey = providerSecret(db, userId, "OPENAI");
  const selectedProvider = providers.deepL ?? deepLProvider;
  const postEditor = providers.openAI ?? openAIProvider;
  const providerTerms = effective.map((term) => ({
    sourceTerm: term.sourceTerm,
    targetTerm: term.targetTerm,
  }));

  const initialTranslation = await selectedProvider.translate(
    {
      originalText,
      sourceLanguage,
      targetLanguage,
      terms: providerTerms,
    },
    deepLKey,
  );
  const translatedText = await postEditor.translate(
    {
      originalText,
      initialTranslation,
      sourceLanguage,
      targetLanguage,
      terms: providerTerms,
    },
    openAIKey,
  );
  return { translatedText, executionStatus: "SUCCESS" };
}
