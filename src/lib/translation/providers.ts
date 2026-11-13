import { randomUUID } from "node:crypto";

import { AppError } from "../domain/errors.ts";
import type { Provider } from "../domain/types.ts";
import { safeRecord } from "../observability/metrics.ts";
import { ATTR, type ExternalOperation, type ExternalProvider } from "../observability/schema.ts";

export type ProviderInput = {
  originalText: string;
  initialTranslation?: string;
  sourceLanguage: string;
  targetLanguage: string;
  terms: Array<{ sourceTerm: string; targetTerm: string }>;
};

export type TranslationProvider = {
  translate(input: ProviderInput, apiKey: string): Promise<string>;
};

export class ProviderError extends AppError {
  readonly provider: Provider;

  constructor(provider: Provider, message: string, details?: Record<string, unknown>) {
    super(502, `${provider}_FAILED`, message, details);
    this.name = "ProviderError";
    this.provider = provider;
  }
}

function deepLBaseUrl(): string {
  return (process.env.DEEPL_API_URL ?? "https://api-free.deepl.com/v2/translate").replace(
    /\/translate\/?$/,
    "",
  );
}


function deepLHeaders(
  apiKey: string,
  contentType?: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `DeepL-Auth-Key ${apiKey}`,
  };

  if (contentType) {
    headers["Content-Type"] = contentType;
  }

  const endpoint = new URL(
    process.env.DEEPL_API_URL ??
      "https://api-free.deepl.com/v2/translate",
  );

  if (endpoint.hostname === "d7xjhnc06nb75.cloudfront.net") {
    if (
      endpoint.protocol !== "https:" ||
      endpoint.pathname !== "/v2/translate"
    ) {
      throw new ProviderError(
        "DEEPL",
        "Invalid DeepL proxy endpoint",
      );
    }

    const token = process.env.DEEPL_PROXY_TOKEN?.trim();

    if (!token) {
      throw new ProviderError(
        "DEEPL",
        "DeepL proxy token is not configured",
      );
    }

    headers["x-gms-proxy-token"] = token;
  }

  return headers;
}

async function responseJson(response: Response): Promise<Record<string, unknown>> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function fetchErrorType(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  if (name === "TimeoutError") return "timeout";
  if (name === "AbortError") return "aborted";
  return "network_error";
}

function serverAddress(url: string): string | undefined {
  try {
    return new URL(url).hostname;
  } catch {
    return undefined;
  }
}

/**
 * Client-side latency of one external HTTP call, from request start until the
 * response body has been read (As-To-Be §5.7). Records only bounded
 * attributes: provider, operation, configured host, status code, error type.
 * Request/response bodies, URLs with IDs and API keys are never recorded.
 */
async function observeExternal<T>(
  provider: ExternalProvider,
  operation: ExternalOperation,
  url: string,
  method: string,
  call: () => Promise<{ response: Response; result: T }>,
): Promise<{ response: Response; result: T }> {
  const start = performance.now();
  let statusCode: number | undefined;
  let errorType: string | undefined;
  try {
    const outcome = await call();
    statusCode = outcome.response.status;
    if (!outcome.response.ok) {
      errorType = String(outcome.response.status);
    }
    return outcome;
  } catch (error) {
    errorType = fetchErrorType(error);
    throw error;
  } finally {
    const seconds = (performance.now() - start) / 1000;
    const host = serverAddress(url);
    safeRecord(({ externalDuration }) =>
      externalDuration.record(seconds, {
        [ATTR.provider]: provider,
        [ATTR.externalOperation]: operation,
        "http.request.method": method,
        ...(host ? { "server.address": host } : {}),
        ...(statusCode !== undefined ? { "http.response.status_code": statusCode } : {}),
        ...(errorType ? { "error.type": errorType } : {}),
      }),
    );
  }
}

export const deepLProvider: TranslationProvider = {
  async translate(input, apiKey) {
    let glossaryId: string | undefined;
    try {
      if (input.terms.length > 0) {
        const glossaryUrl = `${deepLBaseUrl()}/glossaries`;
        const { response: glossaryResponse, result: glossary } = await observeExternal(
          "deepl",
          "glossary_create",
          glossaryUrl,
          "POST",
          async () => {
            const response = await fetch(glossaryUrl, {
              method: "POST",
              headers: deepLHeaders(apiKey, "application/json"),
              body: JSON.stringify({
                name: `gms-${randomUUID()}`,
                source_lang: input.sourceLanguage,
                target_lang: input.targetLanguage,
                entries_format: "tsv",
                entries: input.terms
                  .map(({ sourceTerm, targetTerm }) => `${sourceTerm}\t${targetTerm}`)
                  .join("\n"),
              }),
              cache: "no-store",
            });
            return { response, result: response.ok ? await responseJson(response) : {} };
          },
        );
        if (!glossaryResponse.ok) {
          throw new ProviderError("DEEPL", "DeepL glossary creation failed");
        }
        glossaryId = typeof glossary.glossary_id === "string" ? glossary.glossary_id : undefined;
        if (!glossaryId) {
          throw new ProviderError("DEEPL", "DeepL glossary response was invalid");
        }
      }

      const body = new URLSearchParams({
        text: input.originalText,
        source_lang: input.sourceLanguage,
        target_lang: input.targetLanguage,
      });
      if (glossaryId) {
        body.set("glossary_id", glossaryId);
      }
      const translateUrl = process.env.DEEPL_API_URL ?? "https://api-free.deepl.com/v2/translate";
      const headers = deepLHeaders(
        apiKey,
        "application/x-www-form-urlencoded",
      );
      const { response, result: payload } = await observeExternal(
        "deepl",
        "translate",
        translateUrl,
        "POST",
        async () => {
          const response = await fetch(translateUrl, {
            method: "POST",
            headers,
            body,
            cache: "no-store",
          });
          return { response, result: await responseJson(response) };
        },
      );
      const translations = payload.translations;
      const text =
        Array.isArray(translations) &&
        typeof translations[0] === "object" &&
        translations[0] !== null &&
        typeof (translations[0] as { text?: unknown }).text === "string"
          ? (translations[0] as { text: string }).text
          : null;
      if (!response.ok || !text) {
        throw new ProviderError("DEEPL", "DeepL translation failed");
      }
      return text;
    } catch (error) {
      if (error instanceof ProviderError) {
        throw error;
      }
      throw new ProviderError("DEEPL", "DeepL request failed");
    } finally {
      if (glossaryId) {
        const deleteUrl = `${deepLBaseUrl()}/glossaries/${encodeURIComponent(glossaryId)}`;
        await observeExternal("deepl", "glossary_delete", deleteUrl, "DELETE", async () => {
          const response = await fetch(deleteUrl, {
            method: "DELETE",
            headers: deepLHeaders(apiKey),
            cache: "no-store",
          });
          return { response, result: undefined };
        }).catch(() => undefined);
      }
    }
  },
};

export const openAIProvider: TranslationProvider = {
  async translate(input, apiKey) {
    const openAIUrl = process.env.OPENAI_API_URL ?? "https://api.openai.com/v1/chat/completions";
    const { response, result: payload } = await observeExternal("openai", "post_edit", openAIUrl, "POST", async () => {
      const response = await fetch(openAIUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: "gpt-6-luna",
          reasoning_effort: "medium",
          messages: [
            {
              role: "system",
              content:
                "Post-edit the translation. Return only the final translated text. Preserve meaning and apply the selected terminology.",
            },
            {
              role: "user",
              content: JSON.stringify({
                originalText: input.originalText,
                initialTranslation: input.initialTranslation,
                sourceLanguage: input.sourceLanguage,
                targetLanguage: input.targetLanguage,
                terms: input.terms,
              }),
            },
          ],
        }),
        cache: "no-store",
      });
      return { response, result: await responseJson(response) };
    });
    const choice = Array.isArray(payload.choices) ? payload.choices[0] : null;
    const message = choice && typeof choice === "object" ? (choice as { message?: unknown }).message : null;
    const content =
      message && typeof message === "object" && typeof (message as { content?: unknown }).content === "string"
        ? (message as { content: string }).content
        : null;
    const finishReason =
      choice && typeof choice === "object" && typeof (choice as { finish_reason?: unknown }).finish_reason === "string"
        ? (choice as { finish_reason: string }).finish_reason
        : undefined;
    if (!response.ok || !content || finishReason === "length" || finishReason === "stop" && content.trim() === "") {
      throw new ProviderError("OPENAI", "OpenAI post-editing failed");
    }
    return content;
  },
};
