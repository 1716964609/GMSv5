export type ErrorDetails = Record<string, unknown> | undefined;

export class AppError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetails;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: ErrorDetails,
  ) {
    super(message);
    this.name = "AppError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function isUniqueConstraintError(error: unknown): boolean {
  return error instanceof Error && /UNIQUE constraint failed/i.test(error.message);
}

export function isForeignKeyError(error: unknown): boolean {
  return error instanceof Error && /FOREIGN KEY constraint failed/i.test(error.message);
}

export function isCheckConstraintError(error: unknown): boolean {
  return error instanceof Error && /CHECK constraint failed/i.test(error.message);
}
