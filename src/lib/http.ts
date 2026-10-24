import { NextResponse } from "next/server";

import { AppError } from "./domain/errors.ts";

export async function readJson(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    throw new AppError(400, "INVALID_JSON", "Request body must be valid JSON");
  }
}

export function json(data: unknown, status = 200): NextResponse {
  return NextResponse.json(data, { status });
}

export function errorResponse(error: unknown): NextResponse {
  if (error instanceof AppError) {
    return NextResponse.json(
      {
        error: {
          code: error.code,
          message: error.message,
          ...(error.details ? { details: error.details } : {}),
        },
      },
      { status: error.status },
    );
  }

  console.error("Unhandled application error", error instanceof Error ? error.message : error);
  return NextResponse.json(
    { error: { code: "INTERNAL_ERROR", message: "Unexpected server error" } },
    { status: 500 },
  );
}

export function withErrorHandling(
  handler: (request: Request, context?: unknown) => Promise<Response>,
): (request: Request, context?: unknown) => Promise<Response> {
  return async (request, context) => {
    try {
      return await handler(request, context);
    } catch (error) {
      return errorResponse(error);
    }
  };
}
