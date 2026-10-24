export type AccountStatus =
  | "PENDING_VERIFICATION"
  | "ACTIVE"
  | "PENDING_DELETION";

export type Provider = "DEEPL" | "OPENAI";

export type User = {
  userId: string;
  email: string;
  displayName: string | null;
  authSubject: string;
  emailVerifiedAt: string | null;
  accountStatus: AccountStatus;
  createdAt: string;
  updatedAt: string;
  deletionRequestedAt: string | null;
  deletionScheduledAt: string | null;
};

export type Glossary = {
  glossaryId: string;
  ownerUserId: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
};

export type Term = {
  termId: string;
  glossaryId: string;
  sourceLanguage: string;
  targetLanguage: string;
  sourceTerm: string;
  targetTerm: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
};

export type GlossaryPage = {
  items: Glossary[];
  page: number;
  pageSize: number;
  total: number;
  hasNextPage: boolean;
};

export type TermPage = {
  items: Term[];
  page: number;
  pageSize: number;
  total: number;
  hasNextPage: boolean;
};

export type CredentialSummary = {
  provider: Provider;
  configured: true;
  updatedAt: string;
};

export type TranslationTerm = Term & {
  glossaryName: string;
};

export type ConflictCandidate = Pick<
  TranslationTerm,
  "termId" | "glossaryId" | "glossaryName" | "sourceTerm" | "targetTerm"
>;

export type TranslationConflict = {
  sourceLanguage: string;
  targetLanguage: string;
  sourceTerm: string;
  candidates: ConflictCandidate[];
};
