import type { DatabaseSync } from "node:sqlite";

import type { Span } from "@opentelemetry/api";

import { safeRecord, secondsSince } from "../observability/metrics.ts";
import { ATTR, SPAN, type TranslationOutcome } from "../observability/schema.ts";
import {
  classifyError,
  withSpan,
  withSpanSync,
} from "../observability/spans.ts";

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
  const terms = withSpanSync(
    SPAN.sqliteReadTranslationTerms,
    () => findTranslationTerms(
      db,
      userId,
      input.selectedTermIds,
    ),
    { attributes: { [ATTR.selectedTermCount]: input.selectedTermIds.length } },
  );
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

type TranslationProviders = {
  deepL?: TranslationProvider;
  openAI?: TranslationProvider;
};

/**
 * Outcome classification for the Translation Success Rate SLI candidate
 * (As-To-Be §8.1): user-input errors and term conflicts are distinguished
 * from external dependency failures and internal faults.
 */
export function translationOutcome(error: unknown): TranslationOutcome {
  if (error instanceof TranslationConflictError) {
    return "conflict";
  }
  const { errorClass } = classifyError(error);
  if (errorClass === "client") return "client_error";
  if (errorClass === "dependency") return "dependency_error";
  return "server_error";
}

/**
 * Telemetry boundary of the translation use case. Records only counts,
 * lengths and outcome codes — never the original text, the DeepL
 * intermediate translation, the OpenAI output, terms or API keys
 * (As-To-Be §9.1).
 */
export async function translateUseCase(
  db: DatabaseSync,
  userId: string,
  input: TranslationRequestInput,
  providers: TranslationProviders = {},
): Promise<TranslationResult> {
  const start = performance.now();
  const selectedCount = Array.isArray(input?.selectedTermIds) ? input.selectedTermIds.length : undefined;
  return withSpan(
    SPAN.translationRequest,
    async (span) => {
      let outcome: TranslationOutcome = "success";
      let errorType: string | undefined;
      try {
        return await executeTranslation(db, userId, input, providers, span);
      } catch (error) {
        outcome = translationOutcome(error);
        errorType = classifyError(error).errorType;
        throw error;
      } finally {
        span.setAttribute(ATTR.translationOutcome, outcome);
        const seconds = secondsSince(start);
        safeRecord(({ translationDuration, translationSelectedTerms }) => {
          translationDuration.record(seconds, {
            [ATTR.translationOutcome]: outcome,
            ...(errorType ? { "error.type": errorType } : {}),
          });
          if (selectedCount !== undefined) {
            translationSelectedTerms.record(selectedCount, { [ATTR.translationOutcome]: outcome });
          }
        });
      }
    },
    selectedCount !== undefined ? { attributes: { [ATTR.selectedTermCount]: selectedCount } } : {},
  );
}

async function executeTranslation(
  db: DatabaseSync,
  userId: string,
  input: TranslationRequestInput,
  providers: TranslationProviders,
  requestSpan: Span,
): Promise<TranslationResult> {
  const { originalText, sourceLanguage, targetLanguage } = withSpanSync(SPAN.translationValidate, () => {
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
    return { originalText, sourceLanguage, targetLanguage };
  });
  // Length only (UTF-16 code units); the text itself is never recorded.
  requestSpan.setAttribute(ATTR.sourceTextLength, originalText.length);

  const normalizedInput = {
    ...input,
    originalText,
    sourceLanguage,
    targetLanguage,
  };
  const terms = selectedTerms(db, userId, normalizedInput);
  const effective = withSpanSync(SPAN.translationResolveTerms, (span) => {
    try {
      const resolved = effectiveTerms(terms, normalizedInput);
      span.setAttribute(ATTR.effectiveTermCount, resolved.length);
      return resolved;
    } catch (error) {
      if (error instanceof TranslationConflictError) {
        span.setAttribute(ATTR.conflictCount, error.conflicts.length);
      }
      throw error;
    }
  });
  requestSpan.setAttribute(ATTR.effectiveTermCount, effective.length);
  const { deepLKey, openAIKey } = withSpanSync(SPAN.credentialsLoad, () => ({
    deepLKey: providerSecret(db, userId, "DEEPL"),
    openAIKey: providerSecret(db, userId, "OPENAI"),
  }));
  const selectedProvider = providers.deepL ?? deepLProvider;
  const postEditor = providers.openAI ?? openAIProvider;
  const providerTerms = effective.map((term) => ({
    sourceTerm: term.sourceTerm,
    targetTerm: term.targetTerm,
  }));

  const initialTranslation = await withSpan(
    SPAN.deeplTranslate,
    () =>
      selectedProvider.translate(
        {
          originalText,
          sourceLanguage,
          targetLanguage,
          terms: providerTerms,
        },
        deepLKey,
      ),
    { attributes: { [ATTR.provider]: "deepl", [ATTR.effectiveTermCount]: providerTerms.length } },
  );
  const translatedText = await withSpan(
    SPAN.openaiPostEdit,
    () =>
      postEditor.translate(
        {
          originalText,
          initialTranslation,
          sourceLanguage,
          targetLanguage,
          terms: providerTerms,
        },
        openAIKey,
      ),
    { attributes: { [ATTR.provider]: "openai", [ATTR.effectiveTermCount]: providerTerms.length } },
  );
  return { translatedText, executionStatus: "SUCCESS" };
}
