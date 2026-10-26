import { randomUUID } from "node:crypto";

import { AppError } from "../domain/errors.ts";
import type { Provider } from "../domain/types.ts";

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

export const deepLProvider: TranslationProvider = {
  async translate(input, apiKey) {
    let glossaryId: string | undefined;
    try {
      if (input.terms.length > 0) {
        const glossaryResponse = await fetch(`${deepLBaseUrl()}/glossaries`, {
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
        if (!glossaryResponse.ok) {
          throw new ProviderError("DEEPL", "DeepL glossary creation failed");
        }
        const glossary = await responseJson(glossaryResponse);
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
      const response = await fetch(process.env.DEEPL_API_URL ?? "https://api-free.deepl.com/v2/translate", {
        method: "POST",
        headers: deepLHeaders(
          apiKey,
          "application/x-www-form-urlencoded",
        ),
        body,
        cache: "no-store",
      });
      const payload = await responseJson(response);
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
        await fetch(`${deepLBaseUrl()}/glossaries/${encodeURIComponent(glossaryId)}`, {
          method: "DELETE",
          headers: deepLHeaders(apiKey),
          cache: "no-store",
        }).catch(() => undefined);
      }
    }
  },
};

export const openAIProvider: TranslationProvider = {
  async translate(input, apiKey) {
    const response = await fetch(
      process.env.OPENAI_API_URL ?? "https://api.openai.com/v1/chat/completions",
      {
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
      },
    );
    const payload = await responseJson(response);
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
