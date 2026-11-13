import { AppError } from "@/lib/domain/errors";
import { isRumIngestionEnabled } from "@/lib/observability/config";
import {
  isRumRejection,
  recordRumEvents,
  RUM_MAX_BODY_BYTES,
  validateRumPayload,
} from "@/lib/observability/rum-ingest";
import { protectedRequest } from "../../_utils";

/**
 * Browser RUM beacon endpoint (authenticated, same-origin only).
 * Disabled unless GMS_OTEL_ENABLED=true and GMS_RUM_ENABLED=true; when
 * disabled it discards the body without touching the database.
 */
export async function POST(request: Request): Promise<Response> {
  if (!isRumIngestionEnabled()) {
    return new Response(null, { status: 204 });
  }
  return protectedRequest(request, async () => {
    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (declaredLength > RUM_MAX_BODY_BYTES) {
      throw new AppError(413, "RUM_PAYLOAD_TOO_LARGE", "RUM payload is too large");
    }
    const text = await request.text();
    if (Buffer.byteLength(text, "utf8") > RUM_MAX_BODY_BYTES) {
      throw new AppError(413, "RUM_PAYLOAD_TOO_LARGE", "RUM payload is too large");
    }
    let payload: unknown;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new AppError(400, "INVALID_JSON", "Request body must be valid JSON");
    }
    try {
      recordRumEvents(validateRumPayload(payload));
    } catch (error) {
      if (isRumRejection(error)) {
        throw new AppError(400, "INVALID_RUM_PAYLOAD", "RUM payload is invalid");
      }
      throw error;
    }
    return new Response(null, { status: 204 });
  });
}
