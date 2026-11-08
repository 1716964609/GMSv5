"use client";

import { useEffect, useRef, useState } from "react";

import type { Glossary, Term, TermPage, TranslationConflict } from "@/lib/domain/types";
import { AppShell } from "../app-shell";
import { fetchAllPages } from "@/lib/translation/term-page-loader";
import { isAbortError, TermRequestCoordinator } from "@/lib/translation/term-requests";
import {
  collectCompletePages,
  toggleGlossaryExpansion,
  toggleGlossarySelection,
  toggleTermSelection,
} from "@/lib/translation/term-selection";

type ApiError = {
  error?: {
    message?: string;
    details?: { conflicts?: TranslationConflict[] };
  };
};

type GlossaryTermPages = Record<string, Record<number, TermPage>>;
type GlossaryCurrentPages = Record<string, number>;
type GlossaryLoadState = { kind: "page"; page: number } | { kind: "select-all" };

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: { "content-type": "application/json", ...(options?.headers ?? {}) },
  });
  const payload = (await response.json().catch(() => ({}))) as T & ApiError;
  if (!response.ok) {
    const error = new Error(payload.error?.message ?? "翻訳に失敗しました") as Error & { conflicts?: TranslationConflict[] };
    error.conflicts = payload.error?.details?.conflicts;
    throw error;
  }
  return payload;
}

function pageMap(pages: Record<number, TermPage> | undefined): Map<number, TermPage> {
  return new Map(Object.entries(pages ?? {}).map(([page, value]) => [Number(page), value]));
}

export default function TranslationPage() {
  const [glossaries, setGlossaries] = useState<Glossary[]>([]);
  const [glossaryError, setGlossaryError] = useState("");
  const [termPages, setTermPages] = useState<GlossaryTermPages>({});
  const [currentPages, setCurrentPages] = useState<GlossaryCurrentPages>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadStates, setLoadStates] = useState<Record<string, GlossaryLoadState | undefined>>({});
  const [termErrors, setTermErrors] = useState<Record<string, string | undefined>>({});
  const [selectionError, setSelectionError] = useState("");
  const [originalText, setOriginalText] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState("JA");
  const [targetLanguage, setTargetLanguage] = useState("EN");
  const [consent, setConsent] = useState(false);
  const [conflicts, setConflicts] = useState<TranslationConflict[]>([]);
  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const [translatedText, setTranslatedText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const requests = useRef(new TermRequestCoordinator());
  const expandedRef = useRef(expanded);
  const selectedRef = useRef(selected);
  const termPagesRef = useRef(termPages);

  function commitSelected(next: Set<string>) {
    selectedRef.current = next;
    setSelected(next);
  }

  function commitTermPages(glossaryId: string, pages: Record<number, TermPage>) {
    const next = { ...termPagesRef.current, [glossaryId]: { ...termPagesRef.current[glossaryId], ...pages } };
    termPagesRef.current = next;
    setTermPages(next);
  }

  function setGlossaryLoadState(glossaryId: string, state: GlossaryLoadState | undefined) {
    setLoadStates((current) => ({ ...current, [glossaryId]: state }));
  }

  function cancelGlossaryRequest(glossaryId: string) {
    requests.current.cancel(glossaryId);
    setGlossaryLoadState(glossaryId, undefined);
  }

  useEffect(() => {
    const coordinator = requests.current;
    const controller = new AbortController();
    async function loadGlossaries() {
      const items: Glossary[] = [];
      let page = 1;
      let hasNextPage = true;
      while (hasNextPage) {
        const result = await api<{ items: Glossary[]; hasNextPage: boolean }>(`/api/glossaries?page=${page}`, { signal: controller.signal });
        items.push(...result.items);
        hasNextPage = result.hasNextPage;
        page += 1;
      }
      if (!controller.signal.aborted) setGlossaries(items);
    }
    loadGlossaries().catch((reason: unknown) => {
      if (!isAbortError(reason) && !controller.signal.aborted) {
        setGlossaryError(reason instanceof Error ? reason.message : "用語集を読み込めませんでした");
      }
    });
    return () => {
      controller.abort();
      coordinator.cancelAll();
    };
  }, []);

  useEffect(() => {
    expandedRef.current = expanded;
  }, [expanded]);

  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  useEffect(() => {
    termPagesRef.current = termPages;
  }, [termPages]);

  async function loadTermPage(glossaryId: string, page: number) {
    if (!expandedRef.current.has(glossaryId)) return;
    const token = requests.current.begin(glossaryId);
    setGlossaryLoadState(glossaryId, { kind: "page", page });
    setTermErrors((current) => ({ ...current, [glossaryId]: undefined }));
    try {
      const result = await api<TermPage>(`/api/glossaries/${encodeURIComponent(glossaryId)}/terms?page=${page}`, { signal: token.signal });
      if (!requests.current.isCurrent(token) || !expandedRef.current.has(glossaryId)) return;
      commitTermPages(glossaryId, { [page]: result });
      setCurrentPages((current) => ({ ...current, [glossaryId]: page }));
    } catch (reason) {
      if (!requests.current.isCurrent(token) || isAbortError(reason)) return;
      setTermErrors((current) => ({ ...current, [glossaryId]: reason instanceof Error ? reason.message : "用語を読み込めませんでした" }));
    } finally {
      if (requests.current.isCurrent(token)) {
        setGlossaryLoadState(glossaryId, undefined);
        requests.current.finish(token);
      }
    }
  }

  function toggleGlossary(glossaryId: string) {
    const next = toggleGlossaryExpansion(expandedRef.current, glossaryId);
    expandedRef.current = next;
    setExpanded(next);
    if (!next.has(glossaryId)) {
      cancelGlossaryRequest(glossaryId);
      return;
    }
    const firstPage = termPagesRef.current[glossaryId]?.[1];
    if (firstPage) {
      setCurrentPages((current) => ({ ...current, [glossaryId]: current[glossaryId] ?? 1 }));
      return;
    }
    setCurrentPages((current) => ({ ...current, [glossaryId]: 1 }));
    void loadTermPage(glossaryId, 1);
  }

  function goToPage(glossaryId: string, page: number) {
    const cached = termPagesRef.current[glossaryId]?.[page];
    if (cached) {
      cancelGlossaryRequest(glossaryId);
      setTermErrors((current) => ({ ...current, [glossaryId]: undefined }));
      setCurrentPages((current) => ({ ...current, [glossaryId]: page }));
      return;
    }
    void loadTermPage(glossaryId, page);
  }

  function toggleTerm(termId: string) {
    const result = toggleTermSelection(selectedRef.current, termId);
    if (result.status === "limit-exceeded") {
      setSelectionError("翻訳で選択できる用語は最大10,000件です。");
      return;
    }
    setSelectionError("");
    commitSelected(result.selectedIds);
  }

  async function selectAll(glossaryId: string) {
    if (loadStates[glossaryId]?.kind === "select-all") return;
    const cachedPages = pageMap(termPagesRef.current[glossaryId]);
    const cachedTerms = collectCompletePages(cachedPages);
    if (cachedTerms) {
      const result = toggleGlossarySelection(selectedRef.current, cachedTerms.map((term) => term.termId));
      if (result.status === "limit-exceeded") {
        setSelectionError("この用語集をすべて選択すると、10,000件の上限を超えます。既存の選択は変更していません。");
      } else {
        setSelectionError("");
        commitSelected(result.selectedIds);
      }
      return;
    }

    const token = requests.current.begin(glossaryId);
    setGlossaryLoadState(glossaryId, { kind: "select-all" });
    setTermErrors((current) => ({ ...current, [glossaryId]: undefined }));
    try {
      const pages = await fetchAllPages<Term, TermPage>(
        async (page, signal) => api<TermPage>(`/api/glossaries/${encodeURIComponent(glossaryId)}/terms?page=${page}`, { signal }),
        token.signal,
        cachedPages,
      );
      if (!requests.current.isCurrent(token) || !expandedRef.current.has(glossaryId)) return;
      const allTerms = pages.flatMap((page) => page.items);
      const result = toggleGlossarySelection(selectedRef.current, allTerms.map((term) => term.termId));
      if (result.status === "limit-exceeded") {
        setSelectionError("この用語集をすべて選択すると、10,000件の上限を超えます。既存の選択は変更していません。");
      } else {
        setSelectionError("");
        commitSelected(result.selectedIds);
      }
      commitTermPages(glossaryId, Object.fromEntries(pages.map((page) => [page.page, page])));
    } catch (reason) {
      if (!requests.current.isCurrent(token) || isAbortError(reason)) return;
      setTermErrors((current) => ({ ...current, [glossaryId]: reason instanceof Error ? reason.message : "用語を読み込めませんでした" }));
    } finally {
      if (requests.current.isCurrent(token)) {
        setGlossaryLoadState(glossaryId, undefined);
        requests.current.finish(token);
      }
    }
  }

  const selectedCount = selected.size;

  async function translate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ translatedText: string }>("/api/translate", {
        method: "POST",
        body: JSON.stringify({
          originalText,
          sourceLanguage,
          targetLanguage,
          selectedTermIds: [...selectedRef.current],
          consentConfirmed: consent,
          resolutions,
        }),
      });
      setTranslatedText(result.translatedText);
      setConflicts([]);
      setNotice("翻訳が完了しました");
    } catch (reason) {
      const typed = reason as Error & { conflicts?: TranslationConflict[] };
      if (typed.conflicts) {
        setConflicts(typed.conflicts);
        setNotice("翻訳前に用語候補を一つずつ選択してください");
      } else {
        setError(typed.message);
      }
    } finally {
      setBusy(false);
    }
  }

  function download() {
    const blob = new Blob([translatedText], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "gms-translation.txt";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <AppShell title="BYO Translation">
      <div className="mb-5"><p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#6f70a4]">UC-011〜UC-013</p><h1 className="mt-1 text-3xl font-semibold">BYO翻訳</h1><p className="mt-2 text-sm text-[#68687a]">用語選択はこのリクエストだけに適用され、永続Glossaryは変更されません。</p></div>
      <div className="grid gap-5 lg:grid-cols-[1.05fr_0.95fr]">
        <form className="space-y-5 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20" onSubmit={translate}>
          <label className="block text-sm font-semibold">Original Text<textarea className="mt-2 min-h-56 w-full rounded-2xl border border-[#d7d7e6] p-4 leading-7" value={originalText} onChange={(event) => setOriginalText(event.target.value)} required /></label>
          <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm font-semibold">翻訳元言語<input className="mt-2 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={sourceLanguage} onChange={(event) => setSourceLanguage(event.target.value)} required /></label><label className="text-sm font-semibold">翻訳先言語<input className="mt-2 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)} required /></label></div>
          <label className="flex gap-3 rounded-2xl bg-[#f7f7fa] p-4 text-sm leading-6"><input className="mt-1 h-4 w-4" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>原文と選択した用語を、私自身のDeepLおよびOpenAI APIキーで外部送信することに同意します。</span></label>
          <button className="w-full rounded-2xl bg-[#6f70a4] px-5 py-3 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50" disabled={!consent || busy}>{busy ? "翻訳中…" : "Translate"}</button>
          {notice && <p className="rounded-xl bg-[#ececf7] p-3 text-sm text-[#565667]">{notice}</p>}
          {error && <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error} <a className="underline" href="/settings">Settingsを開く</a></p>}
        </form>

        <section className="min-w-0 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-end justify-between"><div><p className="text-sm font-semibold uppercase tracking-[0.15em] text-[#6f70a4]">用語選択</p><h2 className="mt-1 text-xl font-semibold">{selectedCount.toLocaleString()} terms selected</h2></div><span className="text-xs text-[#68687a]">最大10,000件</span></div>
          {selectionError && <p role="alert" className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{selectionError}</p>}
          {glossaryError && <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{glossaryError} <button className="underline" type="button" onClick={() => window.location.reload()}>再読み込み</button></p>}
          <div className="mt-4 space-y-3">{glossaries.map((glossary) => {
            const glossaryId = glossary.glossaryId;
            const isExpanded = expanded.has(glossaryId);
            const pages = pageMap(termPages[glossaryId]);
            const currentPage = currentPages[glossaryId] ?? 1;
            const pageData = pages.get(currentPage);
            const firstPage = pages.get(1);
            const loadState = loadStates[glossaryId];
            const isLoading = Boolean(loadState);
            const allTerms = collectCompletePages(pages);
            const fullySelected = allTerms !== null && allTerms.length > 0 && allTerms.every((term) => selected.has(term.termId));
            const canSelectAll = isExpanded && Boolean(firstPage?.items.length) && !isLoading;
            return <div className="min-w-0 rounded-2xl bg-[#f7f7fa] p-3" key={glossaryId}>
              <div className="flex items-center justify-between gap-3"><button className="min-w-0 truncate text-left font-semibold" type="button" aria-expanded={isExpanded} onClick={() => toggleGlossary(glossaryId)}>{isExpanded ? "▾" : "▸"} {glossary.name}</button>{isExpanded && <button className="shrink-0 rounded-lg border border-[#9b9bc7]/40 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50" type="button" onClick={() => void selectAll(glossaryId)} disabled={!canSelectAll}>{fullySelected ? "Clear All" : "Select All"}</button>}</div>
              {isExpanded && <>
                <div className="mt-3 max-h-[calc((100dvh-13rem)/2)] min-w-0 space-y-2 overflow-y-auto overscroll-contain pr-1 max-lg:max-h-none max-lg:overflow-visible">
                  {loadState && <p role="status" className="text-sm text-[#68687a]">{loadState.kind === "select-all" ? "用語をすべて確認しています…" : "用語を読み込んでいます…"}</p>}
                  {!loadState && termErrors[glossaryId] && <div className="text-sm text-red-700"><p role="alert">{termErrors[glossaryId]}</p><button className="mt-2 underline" type="button" onClick={() => void loadTermPage(glossaryId, currentPage)}>再試行</button></div>}
                  {!loadState && pageData?.items.map((term: Term) => <label className="flex min-w-0 gap-2 rounded-xl bg-white p-2 text-sm" key={term.termId}><input className="mt-0.5 shrink-0" type="checkbox" checked={selected.has(term.termId)} onChange={() => toggleTerm(term.termId)} /><span className="min-w-0 break-words">{term.sourceTerm} → {term.targetTerm}</span></label>)}
                  {!loadState && pageData && pageData.total === 0 && <p className="text-sm text-[#68687a]">空の用語集です。</p>}
                  {!loadState && pageData && pageData.items.length === 0 && pageData.total > 0 && <p className="text-sm text-[#68687a]">このページに用語はありません。</p>}
                </div>
                <div className="mt-3 flex shrink-0 items-center justify-between text-xs text-[#68687a]">
                  <button type="button" disabled={currentPage <= 1 || isLoading} onClick={() => goToPage(glossaryId, currentPage - 1)}>前へ</button>
                  <span>{currentPage}{pageData ? ` / ${Math.max(1, Math.ceil(pageData.total / pageData.pageSize))}` : ""}</span>
                  <button type="button" disabled={!pageData?.hasNextPage || isLoading} onClick={() => goToPage(glossaryId, currentPage + 1)}>次へ</button>
                </div>
              </>}
            </div>;
          })}</div>
          {conflicts.length > 0 && <div className="mt-5 rounded-2xl border border-amber-300 bg-amber-50 p-4"><h3 className="font-semibold text-amber-900">候補を選択してください</h3>{conflicts.map((conflict) => <fieldset className="mt-4" key={`${conflict.sourceLanguage}-${conflict.targetLanguage}-${conflict.sourceTerm}`}><legend className="text-sm font-medium text-amber-900">{conflict.sourceTerm}</legend><div className="mt-2 space-y-2">{conflict.candidates.map((candidate) => <label className="flex gap-2 text-sm" key={candidate.termId}><input type="radio" name={`${conflict.sourceLanguage}-${conflict.targetLanguage}-${conflict.sourceTerm}`} checked={resolutions[conflict.sourceTerm] === candidate.termId} onChange={() => setResolutions({ ...resolutions, [conflict.sourceTerm]: candidate.termId })} />{candidate.targetTerm} <span className="text-xs text-[#68687a]">({candidate.glossaryName})</span></label>)}</div></fieldset>)}</div>}
        </section>
      </div>
      <section className="mt-5 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">Final Translation</h2><button className="rounded-xl border border-[#9b9bc7]/40 px-4 py-2 text-sm font-semibold disabled:opacity-40" disabled={!translatedText} onClick={download}>Download TXT</button></div><div className="mt-3 min-h-32 whitespace-pre-wrap rounded-2xl bg-[#f7f7fa] p-4 text-sm leading-7">{translatedText || "翻訳結果がここに表示されます。"}</div></section>
    </AppShell>
  );
}
