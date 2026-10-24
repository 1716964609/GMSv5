import { randomUUID } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";

import { AppError } from "../domain/errors.ts";
import type {
  AccountStatus,
  CredentialSummary,
  Glossary,
  GlossaryPage,
  Provider,
  Term,
  TermPage,
  TranslationTerm,
  User,
} from "../domain/types.ts";
import { normalizeTermPair } from "./normalization.ts";

type UserRow = {
  user_id: string;
  email: string;
  display_name: string | null;
  auth_subject: string;
  email_verified_at: string | null;
  account_status: AccountStatus;
  created_at: string;
  updated_at: string;
  deletion_requested_at: string | null;
  deletion_scheduled_at: string | null;
};

type GlossaryRow = {
  glossary_id: string;
  owner_user_id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

type TermRow = {
  term_id: string;
  glossary_id: string;
  source_language: string;
  target_language: string;
  source_term: string;
  target_term: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

type TranslationTermRow = TermRow & { glossary_name: string };

type CountRow = { count: number };

const now = () => new Date().toISOString();

function mapUser(row: UserRow): User {
  return {
    userId: row.user_id,
    email: row.email,
    displayName: row.display_name,
    authSubject: row.auth_subject,
    emailVerifiedAt: row.email_verified_at,
    accountStatus: row.account_status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    deletionRequestedAt: row.deletion_requested_at,
    deletionScheduledAt: row.deletion_scheduled_at,
  };
}

function mapGlossary(row: GlossaryRow): Glossary {
  return {
    glossaryId: row.glossary_id,
    ownerUserId: row.owner_user_id,
    name: row.name,
    description: row.description,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapTerm(row: TermRow): Term {
  return {
    termId: row.term_id,
    glossaryId: row.glossary_id,
    sourceLanguage: row.source_language,
    targetLanguage: row.target_language,
    sourceTerm: row.source_term,
    targetTerm: row.target_term,
    description: row.description,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapTranslationTerm(row: TranslationTermRow): TranslationTerm {
  return { ...mapTerm(row), glossaryName: row.glossary_name };
}

function pageValue(page: number): number {
  return (page - 1) * 100;
}

export function findUserById(db: DatabaseSync, userId: string): User | null {
  const row = db
    .prepare("SELECT * FROM users WHERE user_id = ?")
    .get<UserRow>(userId);
  return row ? mapUser(row) : null;
}

export function findUserByAuthSubject(
  db: DatabaseSync,
  authSubject: string,
): User | null {
  const row = db
    .prepare("SELECT * FROM users WHERE auth_subject = ?")
    .get<UserRow>(authSubject);
  return row ? mapUser(row) : null;
}

export function findUserByEmail(db: DatabaseSync, email: string): User | null {
  const row = db
    .prepare("SELECT * FROM users WHERE email = ?")
    .get<UserRow>(email);
  return row ? mapUser(row) : null;
}

export function upsertExternalUser(
  db: DatabaseSync,
  input: {
    authSubject: string;
    email: string;
    emailVerified: boolean;
    displayName?: string | null;
  },
): User {
  const existingBySubject = findUserByAuthSubject(db, input.authSubject);
  const existingByEmail = findUserByEmail(db, input.email);

  if (existingByEmail && existingByEmail.authSubject !== input.authSubject) {
    throw new AppError(409, "EMAIL_ALREADY_REGISTERED", "Email address is already registered");
  }

  const existing = existingBySubject ?? existingByEmail;
  const timestamp = now();
  const status: AccountStatus = input.emailVerified
    ? "ACTIVE"
    : "PENDING_VERIFICATION";

  if (!existing) {
    const userId = randomUUID();
    db.prepare(
      `INSERT INTO users (
        user_id, email, display_name, auth_subject, email_verified_at,
        account_status, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      userId,
      input.email,
      input.displayName ?? null,
      input.authSubject,
      input.emailVerified ? timestamp : null,
      status,
      timestamp,
      timestamp,
    );
    return findUserById(db, userId)!;
  }

  if (existing.accountStatus === "PENDING_DELETION") {
    return existing;
  }

  db.prepare(
    `UPDATE users
     SET email = ?,
         display_name = COALESCE(?, display_name),
         email_verified_at = ?,
         account_status = ?,
         updated_at = ?
     WHERE user_id = ?`,
  ).run(
    input.email,
    input.displayName ?? null,
    input.emailVerified ? existing.emailVerifiedAt ?? timestamp : null,
    status,
    timestamp,
    existing.userId,
  );

  return findUserById(db, existing.userId)!;
}

export function updateDisplayName(
  db: DatabaseSync,
  userId: string,
  displayName: string | null,
): User {
  const timestamp = now();
  db.prepare(
    "UPDATE users SET display_name = ?, updated_at = ? WHERE user_id = ? AND account_status = 'ACTIVE'",
  ).run(displayName, timestamp, userId);
  const user = findUserById(db, userId);
  if (!user) {
    throw new AppError(404, "USER_NOT_FOUND", "User was not found");
  }
  return user;
}

export function listGlossaries(
  db: DatabaseSync,
  userId: string,
  page: number,
): GlossaryPage {
  const total = db
    .prepare("SELECT COUNT(*) AS count FROM glossaries WHERE owner_user_id = ?")
    .get<CountRow>(userId)?.count ?? 0;
  const items = db
    .prepare(
      `SELECT * FROM glossaries
       WHERE owner_user_id = ?
       ORDER BY created_at DESC, glossary_id ASC
       LIMIT 100 OFFSET ?`,
    )
    .all<GlossaryRow>(userId, pageValue(page))
    .map(mapGlossary);

  return {
    items,
    page,
    pageSize: 100,
    total,
    hasNextPage: page * 100 < total,
  };
}

export function listAllGlossaries(db: DatabaseSync, userId: string): Glossary[] {
  return db
    .prepare(
      `SELECT * FROM glossaries
       WHERE owner_user_id = ?
       ORDER BY name COLLATE BINARY ASC, glossary_id ASC`,
    )
    .all<GlossaryRow>(userId)
    .map(mapGlossary);
}

export function findGlossaryForOwner(
  db: DatabaseSync,
  userId: string,
  glossaryId: string,
): Glossary | null {
  const row = db
    .prepare(
      "SELECT * FROM glossaries WHERE glossary_id = ? AND owner_user_id = ?",
    )
    .get<GlossaryRow>(glossaryId, userId);
  return row ? mapGlossary(row) : null;
}

export function createGlossary(
  db: DatabaseSync,
  userId: string,
  name: string,
  description: string | null,
): Glossary {
  const glossaryId = randomUUID();
  const timestamp = now();
  db.prepare(
    `INSERT INTO glossaries
      (glossary_id, owner_user_id, name, description, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(glossaryId, userId, name, description, timestamp, timestamp);
  return findGlossaryForOwner(db, userId, glossaryId)!;
}

export function updateGlossary(
  db: DatabaseSync,
  userId: string,
  glossaryId: string,
  name: string,
  description: string | null,
): Glossary {
  const timestamp = now();
  db.prepare(
    `UPDATE glossaries
     SET name = ?, description = ?, updated_at = ?
     WHERE glossary_id = ? AND owner_user_id = ?`,
  ).run(name, description, timestamp, glossaryId, userId);
  const glossary = findGlossaryForOwner(db, userId, glossaryId);
  if (!glossary) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  return glossary;
}

export function deleteGlossary(
  db: DatabaseSync,
  userId: string,
  glossaryId: string,
): void {
  const glossary = findGlossaryForOwner(db, userId, glossaryId);
  if (!glossary) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  db.prepare("DELETE FROM glossaries WHERE glossary_id = ? AND owner_user_id = ?").run(
    glossaryId,
    userId,
  );
}

export function listTerms(
  db: DatabaseSync,
  userId: string,
  glossaryId: string,
  page: number,
): TermPage {
  const glossary = findGlossaryForOwner(db, userId, glossaryId);
  if (!glossary) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }

  const total = db
    .prepare("SELECT COUNT(*) AS count FROM terms WHERE glossary_id = ?")
    .get<CountRow>(glossaryId)?.count ?? 0;
  const items = db
    .prepare(
      `SELECT * FROM terms
       WHERE glossary_id = ?
       ORDER BY created_at DESC, term_id ASC
       LIMIT 100 OFFSET ?`,
    )
    .all<TermRow>(glossaryId, pageValue(page))
    .map(mapTerm);

  return {
    items,
    page,
    pageSize: 100,
    total,
    hasNextPage: page * 100 < total,
  };
}

export function findTermForOwner(
  db: DatabaseSync,
  userId: string,
  termId: string,
): Term | null {
  const row = db
    .prepare(
      `SELECT t.* FROM terms t
       JOIN glossaries g ON g.glossary_id = t.glossary_id
       WHERE t.term_id = ? AND g.owner_user_id = ?`,
    )
    .get<TermRow>(termId, userId);
  return row ? mapTerm(row) : null;
}

export function createTerm(
  db: DatabaseSync,
  userId: string,
  input: {
    glossaryId: string;
    sourceLanguage: string;
    targetLanguage: string;
    sourceTerm: string;
    targetTerm: string;
    description: string | null;
  },
): Term {
  if (!findGlossaryForOwner(db, userId, input.glossaryId)) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  const { sourceTerm, targetTerm } = normalizeTermPair(
    input.sourceTerm,
    input.targetTerm,
  );
  const termId = randomUUID();
  const timestamp = now();
  db.prepare(
    `INSERT INTO terms (
      term_id, glossary_id, source_language, target_language,
      source_term, target_term, description, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    termId,
    input.glossaryId,
    input.sourceLanguage,
    input.targetLanguage,
    sourceTerm,
    targetTerm,
    input.description,
    timestamp,
    timestamp,
  );
  return findTermForOwner(db, userId, termId)!;
}

export function updateTerm(
  db: DatabaseSync,
  userId: string,
  termId: string,
  input: {
    glossaryId: string;
    sourceLanguage: string;
    targetLanguage: string;
    sourceTerm: string;
    targetTerm: string;
    description: string | null;
  },
): Term {
  if (!findGlossaryForOwner(db, userId, input.glossaryId)) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  const existing = findTermForOwner(db, userId, termId);
  if (!existing) {
    throw new AppError(404, "TERM_NOT_FOUND", "Term was not found");
  }
  const normalized = normalizeTermPair(input.sourceTerm, input.targetTerm);
  db.prepare(
    `UPDATE terms
     SET glossary_id = ?, source_language = ?, target_language = ?,
         source_term = ?, target_term = ?, description = ?, updated_at = ?
     WHERE term_id = ?`,
  ).run(
    input.glossaryId,
    input.sourceLanguage,
    input.targetLanguage,
    normalized.sourceTerm,
    normalized.targetTerm,
    input.description,
    now(),
    termId,
  );
  return findTermForOwner(db, userId, termId)!;
}

export function deleteTerm(db: DatabaseSync, userId: string, termId: string): void {
  if (!findTermForOwner(db, userId, termId)) {
    throw new AppError(404, "TERM_NOT_FOUND", "Term was not found");
  }
  db.prepare("DELETE FROM terms WHERE term_id = ?").run(termId);
}

export function searchTerms(
  db: DatabaseSync,
  userId: string,
  keyword: string,
): Array<Term & { glossaryName: string }> {
  const pattern = `%${keyword.replace(/[\\%_]/g, "\\$&")}%`;
  return db
    .prepare(
      `SELECT t.*, g.name AS glossary_name FROM terms t
       JOIN glossaries g ON g.glossary_id = t.glossary_id
       WHERE g.owner_user_id = ?
         AND (t.source_term LIKE ? ESCAPE '\\' OR t.target_term LIKE ? ESCAPE '\\')
       ORDER BY t.source_term COLLATE BINARY ASC, t.term_id ASC`,
    )
    .all<TermRow & { glossary_name: string }>(userId, pattern, pattern)
    .map((row) => ({ ...mapTerm(row), glossaryName: row.glossary_name }));
}

export function findTranslationTerms(
  db: DatabaseSync,
  userId: string,
  termIds: string[],
): TranslationTerm[] {
  if (termIds.length === 0) {
    return [];
  }
  const placeholders = termIds.map(() => "?").join(", ");
  return db
    .prepare(
      `SELECT t.*, g.name AS glossary_name FROM terms t
       JOIN glossaries g ON g.glossary_id = t.glossary_id
       WHERE g.owner_user_id = ? AND t.term_id IN (${placeholders})`,
    )
    .all<TranslationTermRow>(userId, ...termIds)
    .map(mapTranslationTerm);
}

export function listCredentialSummaries(
  db: DatabaseSync,
  userId: string,
): CredentialSummary[] {
  return db
    .prepare(
      `SELECT provider, updated_at FROM api_credentials
       WHERE user_id = ? ORDER BY provider ASC`,
    )
    .all<{ provider: Provider; updated_at: string }>(userId)
    .map((row) => ({
      provider: row.provider,
      configured: true,
      updatedAt: row.updated_at,
    }));
}

export function getCredentialSecret(
  db: DatabaseSync,
  userId: string,
  provider: Provider,
): string | null {
  return (
    db
      .prepare(
        "SELECT encrypted_secret FROM api_credentials WHERE user_id = ? AND provider = ?",
      )
      .get<{ encrypted_secret: string }>(userId, provider)?.encrypted_secret ?? null
  );
}

export function upsertCredential(
  db: DatabaseSync,
  userId: string,
  provider: Provider,
  encryptedSecret: string,
): CredentialSummary {
  const timestamp = now();
  db.prepare(
    `INSERT INTO api_credentials
      (credential_id, user_id, provider, encrypted_secret, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT (user_id, provider) DO UPDATE SET
       encrypted_secret = excluded.encrypted_secret,
       updated_at = excluded.updated_at`,
  ).run(randomUUID(), userId, provider, encryptedSecret, timestamp, timestamp);
  return {
    provider,
    configured: true,
    updatedAt: timestamp,
  };
}

export function deleteCredential(
  db: DatabaseSync,
  userId: string,
  provider: Provider,
): void {
  db.prepare("DELETE FROM api_credentials WHERE user_id = ? AND provider = ?").run(
    userId,
    provider,
  );
}

export function requestAccountDeletion(db: DatabaseSync, userId: string): User {
  const current = findUserById(db, userId);
  if (!current) {
    throw new AppError(404, "USER_NOT_FOUND", "User was not found");
  }
  if (current.accountStatus === "PENDING_DELETION") {
    return current;
  }
  if (current.accountStatus !== "ACTIVE") {
    throw new AppError(409, "ACCOUNT_NOT_ACTIVE", "Only an active account can be deleted");
  }

  const requestedAt = new Date();
  const scheduledAt = new Date(requestedAt);
  scheduledAt.setUTCDate(scheduledAt.getUTCDate() + 30);
  const requested = requestedAt.toISOString();
  const scheduled = scheduledAt.toISOString();

  db.exec("BEGIN IMMEDIATE");
  try {
    db.prepare(
      `UPDATE users
       SET account_status = 'PENDING_DELETION',
           deletion_requested_at = ?,
           deletion_scheduled_at = ?,
           updated_at = ?
       WHERE user_id = ? AND account_status = 'ACTIVE'`,
    ).run(requested, scheduled, requested, userId);
    db.prepare("DELETE FROM api_credentials WHERE user_id = ?").run(userId);
    db.exec("COMMIT");
  } catch (error) {
    try {
      db.exec("ROLLBACK");
    } catch {
      // Preserve the original error.
    }
    throw error;
  }

  return findUserById(db, userId)!;
}

export type PurgedAccount = { userId: string; authSubject: string };
type PurgedAccountRow = { user_id: string; auth_subject: string };

export function purgeEligibleAccounts(
  db: DatabaseSync,
  at = new Date().toISOString(),
): PurgedAccount[] {
  const eligible = db
    .prepare(
      `SELECT user_id, auth_subject FROM users
       WHERE account_status = 'PENDING_DELETION'
         AND deletion_scheduled_at IS NOT NULL
         AND deletion_scheduled_at <= ?`,
    )
    .all<PurgedAccountRow>(at);

  if (eligible.length === 0) {
    return [];
  }

  db.exec("BEGIN IMMEDIATE");
  try {
    const deleteUser = db.prepare("DELETE FROM users WHERE user_id = ? AND account_status = 'PENDING_DELETION'");
    for (const account of eligible) {
      deleteUser.run(account.user_id);
    }
    db.exec("COMMIT");
  } catch (error) {
    try {
      db.exec("ROLLBACK");
    } catch {
      // Preserve the original error.
    }
    throw error;
  }

  return eligible.map((account) => ({
    userId: account.user_id,
    authSubject: account.auth_subject,
  }));
}

export function importTermsTransaction(
  db: DatabaseSync,
  userId: string,
  input: {
    glossaryId?: string;
    glossaryName?: string;
    glossaryDescription?: string | null;
    terms: Array<{
      sourceLanguage: string;
      targetLanguage: string;
      sourceTerm: string;
      targetTerm: string;
      description: string | null;
    }>;
  },
): { glossaryId: string; inserted: number } {
  let glossaryId = input.glossaryId;
  if (glossaryId && !findGlossaryForOwner(db, userId, glossaryId)) {
    throw new AppError(404, "GLOSSARY_NOT_FOUND", "Glossary was not found");
  }
  if (!glossaryId && !input.glossaryName) {
    throw new AppError(400, "INVALID_IMPORT_TARGET", "A glossary target is required");
  }

  db.exec("BEGIN IMMEDIATE");
  try {
    if (!glossaryId) {
      glossaryId = randomUUID();
      const timestamp = now();
      db.prepare(
        `INSERT INTO glossaries
          (glossary_id, owner_user_id, name, description, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?)`,
      ).run(
        glossaryId,
        userId,
        input.glossaryName,
        input.glossaryDescription ?? null,
        timestamp,
        timestamp,
      );
    }

    const insert = db.prepare(
      `INSERT INTO terms (
        term_id, glossary_id, source_language, target_language,
        source_term, target_term, description, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    for (const term of input.terms) {
      const normalized = normalizeTermPair(term.sourceTerm, term.targetTerm);
      const timestamp = now();
      insert.run(
        randomUUID(),
        glossaryId,
        term.sourceLanguage,
        term.targetLanguage,
        normalized.sourceTerm,
        normalized.targetTerm,
        term.description,
        timestamp,
        timestamp,
      );
    }
    db.exec("COMMIT");
  } catch (error) {
    try {
      db.exec("ROLLBACK");
    } catch {
      // Preserve the original error.
    }
    throw error;
  }

  return { glossaryId, inserted: input.terms.length };
}
