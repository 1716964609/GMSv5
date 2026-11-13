/**
 * GMS v5 application telemetry schema (version 1).
 *
 * This file is the single source of truth in code for GMS-owned span names,
 * metric names and `gms.*` attribute keys. The redaction layer drops every
 * `gms.*` attribute that is not declared here, so an accidental
 * `span.setAttribute("gms.original_text", text)` can never be exported.
 *
 * Human-readable specification: docs/observability/application-instrumentation.md
 */

export const TELEMETRY_SCHEMA_VERSION = "1";

/** Instrumentation scope (tracer / meter) name for all GMS-owned telemetry. */
export const INSTRUMENTATION_SCOPE = "gms-v5";

export const SPAN = {
  translationRequest: "gms.translation.request",
  translationValidate: "gms.translation.validate",
  translationResolveTerms: "gms.translation.resolve_terms",
  credentialsLoad: "gms.credentials.load",
  /** Kept from the initial PoC instrumentation. */
  sqliteReadTranslationTerms: "gms.sqlite.read_translation_terms",
  /** Kept from the initial PoC instrumentation. */
  deeplTranslate: "gms.deepl.translate",
  openaiPostEdit: "gms.openai.post_edit",
  rumTranslation: "gms.rum.translation",
  rumSelectAll: "gms.rum.select_all",
} as const;

export const METRIC = {
  httpServerDuration: "http.server.request.duration",
  dbClientDuration: "db.client.operation.duration",
  translationDuration: "gms.translation.duration",
  translationSelectedTerms: "gms.translation.selected_terms",
  externalDuration: "gms.external.request.duration",
  rumActionDuration: "gms.rum.user_action.duration",
  rumWebVitalPrefix: "gms.rum.web_vital.",
  rumEventsRejected: "gms.rum.events.rejected",
  eventLoopDelayP50: "nodejs.eventloop.delay.p50",
  eventLoopDelayP99: "nodejs.eventloop.delay.p99",
  eventLoopDelayMax: "nodejs.eventloop.delay.max",
  processMemoryUsage: "process.memory.usage",
  v8HeapUsed: "v8js.memory.heap.used",
  processUptime: "process.uptime",
  sqliteFileSize: "gms.sqlite.file.size",
  sqliteFilesystemAvailable: "gms.sqlite.filesystem.available",
} as const;

export const ATTR = {
  provider: "gms.provider",
  externalOperation: "gms.external.operation",
  translationOutcome: "gms.translation.outcome",
  selectedTermCount: "gms.translation.selected_term_count",
  effectiveTermCount: "gms.translation.effective_term_count",
  conflictCount: "gms.translation.conflict_count",
  sourceTextLength: "gms.translation.source_text_length",
  errorCode: "gms.error.code",
  errorClass: "gms.error.class",
  actionName: "gms.action.name",
  actionOutcome: "gms.action.outcome",
  pageRoute: "gms.page.route",
  telemetrySource: "gms.telemetry.source",
  rumTimingBasis: "gms.rum.timing_basis",
  rumRating: "gms.rum.rating",
  sqliteFile: "gms.sqlite.file",
  sqliteSpansSuppressed: "gms.sqlite.spans_suppressed",
  schemaVersion: "gms.telemetry.schema_version",
} as const;

export const ALLOWED_GMS_ATTRIBUTE_KEYS: ReadonlySet<string> = new Set(Object.values(ATTR));

export type ErrorClass = "client" | "dependency" | "server";

export type TranslationOutcome =
  | "success"
  | "conflict"
  | "client_error"
  | "dependency_error"
  | "server_error";

export type ExternalProvider = "deepl" | "openai";

export type ExternalOperation =
  | "glossary_create"
  | "translate"
  | "glossary_delete"
  | "post_edit";

/** Page routes that may appear as `gms.page.route`; anything else is "other". */
export const RUM_PAGE_ROUTES = [
  "/",
  "/glossary",
  "/translation",
  "/settings",
  "/login",
  "/register",
] as const;

export type RumPageRoute = (typeof RUM_PAGE_ROUTES)[number] | "other";

export const RUM_ACTIONS = {
  translation: ["success", "conflict", "error"],
  select_all: ["success", "limit_exceeded", "cancelled", "error"],
} as const;

export type RumActionName = keyof typeof RUM_ACTIONS;
export type RumActionOutcome<N extends RumActionName = RumActionName> = (typeof RUM_ACTIONS)[N][number];

export const RUM_WEB_VITALS = ["LCP", "INP", "CLS", "FCP", "TTFB"] as const;
export type RumWebVitalName = (typeof RUM_WEB_VITALS)[number];

export function normalizePageRoute(pathname: string): RumPageRoute {
  const trimmed = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return (RUM_PAGE_ROUTES as readonly string[]).includes(trimmed)
    ? (trimmed as RumPageRoute)
    : "other";
}
