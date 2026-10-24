"use client";

import { useEffect, useMemo, useState } from "react";

import type { Glossary, Term, TranslationConflict } from "@/lib/domain/types";
import { AppShell } from "../app-shell";

type TermWithGlossary = Term & { glossaryName: string };

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, headers: { "content-type": "application/json", ...(options?.headers ?? {}) } });
  const payload = (await response.json().catch(() => ({}))) as T & { error?: { message?: string; details?: { conflicts?: TranslationConflict[] } } };
  if (!response.ok) {
    const error = new Error(payload.error?.message ?? "翻訳に失敗しました") as Error & { conflicts?: TranslationConflict[] };
    error.conflicts = payload.error?.details?.conflicts;
    throw error;
  }
  return payload;
}

export default function TranslationPage() {
  const [glossaries, setGlossaries] = useState<Glossary[]>([]);
  const [terms, setTerms] = useState<TermWithGlossary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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

  useEffect(() => {
    async function load() {
      const allGlossaries: Glossary[] = [];
      let glossaryPage = 1;
      let glossaryHasNext = true;
      while (glossaryHasNext) {
        const glossaryResult = await api<{ items: Glossary[]; hasNextPage: boolean }>(`/api/glossaries?page=${glossaryPage}`);
        allGlossaries.push(...glossaryResult.items);
        glossaryHasNext = glossaryResult.hasNextPage;
        glossaryPage += 1;
      }
      setGlossaries(allGlossaries);
      const loaded: TermWithGlossary[] = [];
      for (const glossary of allGlossaries) {
        let termPage = 1;
        let termHasNext = true;
        while (termHasNext) {
          const result = await api<{ items: Term[]; hasNextPage: boolean }>(`/api/glossaries/${glossary.glossaryId}/terms?page=${termPage}`);
          loaded.push(...result.items.map((term) => ({ ...term, glossaryName: glossary.name })));
          termHasNext = result.hasNextPage;
          termPage += 1;
        }
      }
      setTerms(loaded);
    }
    load().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "用語を読み込めませんでした"));
  }, []);

  const groupedTerms = useMemo(() => {
    const groups = new Map<string, TermWithGlossary[]>();
    for (const term of terms) {
      const group = groups.get(term.glossaryId) ?? [];
      group.push(term);
      groups.set(term.glossaryId, group);
    }
    return groups;
  }, [terms]);

  function toggleTerm(termId: string) {
    const next = new Set(selected);
    if (next.has(termId)) next.delete(termId); else if (next.size < 10_000) next.add(termId);
    setSelected(next);
  }

  function toggleGlossary(glossaryId: string) {
    const next = new Set(expanded);
    if (next.has(glossaryId)) next.delete(glossaryId); else next.add(glossaryId);
    setExpanded(next);
  }

  function selectAll(glossaryId: string) {
    const next = new Set(selected);
    const group = groupedTerms.get(glossaryId) ?? [];
    const allSelected = group.every((term) => next.has(term.termId));
    for (const term of group) {
      if (allSelected) next.delete(term.termId); else if (next.size < 10_000) next.add(term.termId);
    }
    setSelected(next);
  }

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
          selectedTermIds: [...selected],
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

        <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-end justify-between"><div><p className="text-sm font-semibold uppercase tracking-[0.15em] text-[#6f70a4]">用語選択</p><h2 className="mt-1 text-xl font-semibold">{selected.size.toLocaleString()} terms selected</h2></div><span className="text-xs text-[#68687a]">最大10,000件</span></div>
          <div className="mt-4 space-y-3">{glossaries.map((glossary) => { const group = groupedTerms.get(glossary.glossaryId) ?? []; const allSelected = group.length > 0 && group.every((term) => selected.has(term.termId)); return <div className="rounded-2xl bg-[#f7f7fa] p-3" key={glossary.glossaryId}><div className="flex items-center justify-between gap-3"><button className="font-semibold" type="button" onClick={() => toggleGlossary(glossary.glossaryId)}>{expanded.has(glossary.glossaryId) ? "▾" : "▸"} {glossary.name}</button><button className="rounded-lg border border-[#9b9bc7]/40 px-2 py-1 text-xs" type="button" onClick={() => selectAll(glossary.glossaryId)} disabled={group.length === 0}>{allSelected ? "Clear All" : "Select All"}</button></div>{expanded.has(glossary.glossaryId) && <div className="mt-3 space-y-2">{group.map((term) => <label className="flex gap-2 rounded-xl bg-white p-2 text-sm" key={term.termId}><input type="checkbox" checked={selected.has(term.termId)} onChange={() => toggleTerm(term.termId)} /><span>{term.sourceTerm} → {term.targetTerm}</span></label>)}{group.length === 0 && <p className="text-sm text-[#68687a]">空の用語集です。</p>}</div>}</div>; })}</div>
          {conflicts.length > 0 && <div className="mt-5 rounded-2xl border border-amber-300 bg-amber-50 p-4"><h3 className="font-semibold text-amber-900">候補を選択してください</h3>{conflicts.map((conflict) => <fieldset className="mt-4" key={`${conflict.sourceLanguage}-${conflict.targetLanguage}-${conflict.sourceTerm}`}><legend className="text-sm font-medium text-amber-900">{conflict.sourceTerm}</legend><div className="mt-2 space-y-2">{conflict.candidates.map((candidate) => <label className="flex gap-2 text-sm" key={candidate.termId}><input type="radio" name={conflict.sourceTerm} checked={resolutions[conflict.sourceTerm] === candidate.termId} onChange={() => setResolutions({ ...resolutions, [conflict.sourceTerm]: candidate.termId })} />{candidate.targetTerm} <span className="text-xs text-[#68687a]">({candidate.glossaryName})</span></label>)}</div></fieldset>)}</div>}
        </section>
      </div>
      <section className="mt-5 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">Final Translation</h2><button className="rounded-xl border border-[#9b9bc7]/40 px-4 py-2 text-sm font-semibold disabled:opacity-40" disabled={!translatedText} onClick={download}>Download TXT</button></div><div className="mt-3 min-h-32 whitespace-pre-wrap rounded-2xl bg-[#f7f7fa] p-4 text-sm leading-7">{translatedText || "翻訳結果がここに表示されます。"}</div></section>
    </AppShell>
  );
}
