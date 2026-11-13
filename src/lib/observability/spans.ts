import {
  trace,
  SpanKind,
  SpanStatusCode,
  type Attributes,
  type Span,
} from "@opentelemetry/api";

import { AppError } from "../domain/errors.ts";
import { ATTR, INSTRUMENTATION_SCOPE, type ErrorClass } from "./schema.ts";

/**
 * Business span helpers (initial PoC API, extended).
 *
 * - `withSpan` / `withSpanSync` keep their original `(name, operation)` call
 *   shape; `options` and the `span` callback argument are optional additions.
 * - Errors are classified without exporting `error.message` (which may carry
 *   user input or provider responses): only the AppError code / error name.
 * - Client errors (4xx AppError) do not mark the span as ERROR, following the
 *   OpenTelemetry HTTP convention; dependency (502/503/504) and server
 *   failures do.
 */

const tracer = trace.getTracer(INSTRUMENTATION_SCOPE);

export type SpanOptions = {
  attributes?: Attributes;
  kind?: SpanKind;
};

export type ClassifiedError = {
  errorClass: ErrorClass;
  /** Low-cardinality identifier: AppError code or JS error name. */
  errorType: string;
};

const DEPENDENCY_STATUSES = new Set([502, 503, 504]);

export function classifyError(error: unknown): ClassifiedError {
  if (error instanceof AppError) {
    const errorClass: ErrorClass =
      error.status < 500 ? "client" : DEPENDENCY_STATUSES.has(error.status) ? "dependency" : "server";
    return { errorClass, errorType: error.code };
  }
  const name = error instanceof Error && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(error.name) ? error.name : "Error";
  return { errorClass: "server", errorType: name };
}

export function recordSpanError(span: Span, error: unknown): ClassifiedError {
  const classified = classifyError(error);
  span.setAttributes({
    "error.type": classified.errorType,
    [ATTR.errorClass]: classified.errorClass,
    ...(error instanceof AppError ? { [ATTR.errorCode]: error.code } : {}),
  });
  if (classified.errorClass !== "client") {
    // Status message is the code only; never the free-text message.
    span.setStatus({ code: SpanStatusCode.ERROR, message: classified.errorType });
    span.addEvent("exception", { "exception.type": error instanceof Error ? error.name : typeof error });
  }
  return classified;
}

export function withSpanSync<T>(
  name: string,
  operation: (span: Span) => T,
  options: SpanOptions = {},
): T {
  return tracer.startActiveSpan(
    name,
    { kind: options.kind ?? SpanKind.INTERNAL, attributes: options.attributes },
    (span) => {
      try {
        return operation(span);
      } catch (error) {
        recordSpanError(span, error);
        throw error;
      } finally {
        span.end();
      }
    },
  );
}

export async function withSpan<T>(
  name: string,
  operation: (span: Span) => Promise<T>,
  options: SpanOptions = {},
): Promise<T> {
  return tracer.startActiveSpan(
    name,
    { kind: options.kind ?? SpanKind.INTERNAL, attributes: options.attributes },
    async (span) => {
      try {
        return await operation(span);
      } catch (error) {
        recordSpanError(span, error);
        throw error;
      } finally {
        span.end();
      }
    },
  );
}
