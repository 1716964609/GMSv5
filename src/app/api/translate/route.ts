import { json, readJson } from "@/lib/http";
import { translateUseCase } from "@/lib/services/translation";
import { AppError } from "@/lib/domain/errors";
import { asRecord, parseBoolean, requiredString } from "@/lib/validation";
import { protectedRequest } from "../_utils";

function translationInput(value: unknown) {
  const record = asRecord(value, "translation request");
  const selectedTermIds = record.selectedTermIds;
  if (!Array.isArray(selectedTermIds)) {
    throw new AppError(400, "INVALID_TERM_SELECTION", "selectedTermIds must be an array");
  }
  const resolutionsValue = record.resolutions;
  let resolutions: Record<string, string> | undefined;
  if (resolutionsValue !== undefined) {
    const candidate = asRecord(resolutionsValue, "resolutions");
    resolutions = {};
    for (const [key, value] of Object.entries(candidate)) {
      resolutions[key] = requiredString(value, `resolutions.${key}`);
    }
  }
  return {
    originalText: requiredString(record.originalText, "originalText"),
    sourceLanguage: requiredString(record.sourceLanguage, "sourceLanguage"),
    targetLanguage: requiredString(record.targetLanguage, "targetLanguage"),
    selectedTermIds: selectedTermIds.map((id) => requiredString(id, "selectedTermIds")),
    consentConfirmed: parseBoolean(record.consentConfirmed, "consentConfirmed"),
    resolutions,
  };
}

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    return json(await translateUseCase(db, user.userId, translationInput(await readJson(request))));
  });
}
