import { AppError } from "./domain/errors.ts";

export const MAX_PAGE_SIZE = 100;
export const MAX_IMPORT_TERMS = 10_000;
export const MAX_TRANSLATION_TERMS = 10_000;

export function asRecord(value: unknown, context = "request"): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new AppError(400, "INVALID_JSON", `${context} must be a JSON object`);
  }

  return value as Record<string, unknown>;
}

export function requiredString(
  value: unknown,
  field: string,
  options: { maxLength?: number } = {},
): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new AppError(400, "INVALID_INPUT", `${field} must be a non-empty string`, {
      field,
    });
  }

  const result = value.trim();
  if (options.maxLength !== undefined && result.length > options.maxLength) {
    throw new AppError(400, "INVALID_INPUT", `${field} is too long`, { field });
  }

  return result;
}

export function optionalString(
  value: unknown,
  field: string,
  options: { maxLength?: number } = {},
): string | null {
  if (value === undefined || value === null) {
    return null;
  }

  if (typeof value !== "string") {
    throw new AppError(400, "INVALID_INPUT", `${field} must be a string`, { field });
  }

  if (options.maxLength !== undefined && value.length > options.maxLength) {
    throw new AppError(400, "INVALID_INPUT", `${field} is too long`, { field });
  }

  return value;
}

export function emailAddress(value: unknown): string {
  const email = requiredString(value, "email", { maxLength: 320 }).toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new AppError(400, "INVALID_EMAIL", "email must be valid");
  }

  return email;
}

export function positivePage(value: string | null): number {
  if (value === null || value === "") {
    return 1;
  }

  const page = Number(value);
  if (!Number.isInteger(page) || page < 1) {
    throw new AppError(400, "INVALID_PAGE", "page must be a positive integer");
  }

  return page;
}

export function parseBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new AppError(400, "INVALID_INPUT", `${field} must be boolean`, { field });
  }

  return value;
}

export function assertNoUnknownKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  context: string,
): void {
  const allowedSet = new Set(allowed);
  const unknown = Object.keys(value).filter((key) => !allowedSet.has(key));
  if (unknown.length > 0) {
    throw new AppError(400, "UNKNOWN_FIELD", `${context} contains unknown fields`, {
      fields: unknown,
    });
  }
}

export function parseId(value: string | null, field: string): string {
  return requiredString(value, field, { maxLength: 200 });
}
