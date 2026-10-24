"use client";

import { useEffect, useState } from "react";

import { AppShell } from "../app-shell";
import type { Glossary, GlossaryPage, Term, TermPage } from "@/lib/domain/types";

type ApiError = { error?: { message?: string } };

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, headers: { "content-type": "application/json", ...(options?.headers ?? {}) } });
  const payload = (await response.json().catch(() => ({}))) as T & ApiError;
  if (!response.ok) {
    throw new Error(payload.error?.message ?? "処理に失敗しました");
  }
  return payload;
}

const emptyTerm = { sourceLanguage: "JA", targetLanguage: "EN", sourceTerm: "", targetTerm: "", description: "" };

export default function GlossaryPage() {
  const [glossaries, setGlossaries] = useState<Glossary[]>([]);
  const [glossaryPage, setGlossaryPage] = useState(1);
  const [glossaryHasNext, setGlossaryHasNext] = useState(false);
  const [selectedGlossary, setSelectedGlossary] = useState<Glossary | null>(null);
  const [terms, setTerms] = useState<Term[]>([]);
  const [termPage, setTermPage] = useState(1);
  const [termHasNext, setTermHasNext] = useState(false);
  const [selectedTerm, setSelectedTerm] = useState<Term | null>(null);
  const [glossaryForm, setGlossaryForm] = useState({ name: "", description: "" });
  const [termForm, setTermForm] = useState(emptyTerm);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<Array<Term & { glossaryName: string }>>([]);
  const [importTarget, setImportTarget] = useState("");
  const [importName, setImportName] = useState("");
  const [importDescription, setImportDescription] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadGlossaries(page = glossaryPage) {
    const result = await api<GlossaryPage>(`/api/glossaries?page=${page}`);
    setGlossaries(result.items);
    setGlossaryPage(result.page);
    setGlossaryHasNext(result.hasNextPage);
    if (selectedGlossary && !result.items.some((item) => item.glossaryId === selectedGlossary.glossaryId)) {
      setSelectedGlossary(null);
      setSelectedTerm(null);
      setTerms([]);
    }
  }

  async function loadTerms(glossaryId: string, page = termPage) {
    const result = await api<TermPage>(`/api/glossaries/${glossaryId}/terms?page=${page}`);
    setTerms(result.items);
    setTermPage(result.page);
    setTermHasNext(result.hasNextPage);
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadGlossaries(1).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "読み込みに失敗しました"));
    }, 0);
    return () => window.clearTimeout(timer);
    // The initial request intentionally runs once when the screen opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function chooseGlossary(glossary: Glossary) {
    setSelectedGlossary(glossary);
    setSelectedTerm(null);
    setTermForm(emptyTerm);
    loadTerms(glossary.glossaryId, 1).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "用語を読み込めませんでした"));
  }

  async function saveGlossary(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const url = selectedGlossary ? `/api/glossaries/${selectedGlossary.glossaryId}` : "/api/glossaries";
      const method = selectedGlossary ? "PATCH" : "POST";
      const saved = await api<Glossary>(url, { method, body: JSON.stringify(glossaryForm) });
      setSelectedGlossary(saved);
      setGlossaryForm({ name: saved.name, description: saved.description ?? "" });
      await loadGlossaries(glossaryPage);
      setMessage("用語集を保存しました");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存に失敗しました");
    }
  }

  async function removeGlossary() {
    if (!selectedGlossary || !window.confirm("用語集と所属用語を削除しますか？")) return;
    try {
      await api(`/api/glossaries/${selectedGlossary.glossaryId}`, { method: "DELETE" });
      setSelectedGlossary(null);
      setSelectedTerm(null);
      setTerms([]);
      setGlossaryForm({ name: "", description: "" });
      await loadGlossaries(glossaryPage);
      setMessage("用語集を削除しました");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "削除に失敗しました");
    }
  }

  async function saveTerm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedGlossary) return;
    setError("");
    try {
      const url = selectedTerm ? `/api/terms/${selectedTerm.termId}` : `/api/glossaries/${selectedGlossary.glossaryId}/terms`;
      const method = selectedTerm ? "PATCH" : "POST";
      const saved = await api<Term>(url, { method, body: JSON.stringify(termForm) });
      setSelectedTerm(saved);
      await loadTerms(selectedGlossary.glossaryId, termPage);
      setMessage("用語を保存しました");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "用語の保存に失敗しました");
    }
  }

  async function removeTerm() {
    if (!selectedTerm || !window.confirm("この用語を削除しますか？")) return;
    try {
      await api(`/api/terms/${selectedTerm.termId}`, { method: "DELETE" });
      setSelectedTerm(null);
      setTermForm(emptyTerm);
      if (selectedGlossary) await loadTerms(selectedGlossary.glossaryId, termPage);
      setMessage("用語を削除しました");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "削除に失敗しました");
    }
  }

  async function runSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const result = await api<{ items: Array<Term & { glossaryName: string }> }>(`/api/search?q=${encodeURIComponent(search)}`);
      setSearchResults(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "検索に失敗しました");
    }
  }

  async function importFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const document = JSON.parse(await file.text()) as unknown;
      const target = importTarget ? { glossaryId: importTarget } : { name: importName, description: importDescription };
      const result = await api<{ glossaryId: string; inserted: number }>("/api/import", { method: "POST", body: JSON.stringify({ target, document }) });
      await loadGlossaries(1);
      setMessage(`${result.inserted}件の用語をインポートしました`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "インポートに失敗しました");
    } finally {
      event.target.value = "";
    }
  }

  return (
    <AppShell title="Glossary Management">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#6f70a4]">UC-006〜UC-010</p><h1 className="mt-1 text-3xl font-semibold">用語集管理</h1></div>
        <div className="text-right text-sm text-[#68687a]"><p>{message || "保存状態は現在値のみです"}</p>{error && <p className="text-red-700">{error}</p>}</div>
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <h2 className="font-semibold">検索</h2>
          <form className="mt-3 flex gap-2" onSubmit={runSearch}><input className="min-w-0 flex-1 rounded-xl border border-[#d7d7e6] px-3 py-2 text-sm" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="原語・訳語" /><button className="rounded-xl bg-[#6f70a4] px-3 py-2 text-sm font-semibold text-white">検索</button></form>
          <div className="mt-4 space-y-2">{searchResults.map((item) => <button className="block w-full rounded-xl bg-[#f7f7fa] p-3 text-left text-sm hover:bg-[#ececf7]" key={item.termId} onClick={() => { const glossary = glossaries.find((candidate) => candidate.glossaryId === item.glossaryId); if (glossary) chooseGlossary(glossary); setSelectedTerm(item); setTermForm({ sourceLanguage: item.sourceLanguage, targetLanguage: item.targetLanguage, sourceTerm: item.sourceTerm, targetTerm: item.targetTerm, description: item.description ?? "" }); }}>{item.sourceTerm} → {item.targetTerm}<span className="mt-1 block text-xs text-[#68687a]">{item.glossaryName}</span></button>)}</div>
        </section>

        <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-center justify-between"><h2 className="font-semibold">用語集一覧</h2><button className="text-sm text-[#6f70a4]" onClick={() => { setSelectedGlossary(null); setSelectedTerm(null); setGlossaryForm({ name: "", description: "" }); }}>新規</button></div>
          <div className="mt-3 space-y-2">{glossaries.map((glossary) => <button className={`block w-full rounded-xl p-3 text-left text-sm ${selectedGlossary?.glossaryId === glossary.glossaryId ? "bg-[#ececf7] ring-1 ring-[#9b9bc7]" : "bg-[#f7f7fa] hover:bg-[#ececf7]"}`} key={glossary.glossaryId} onClick={() => { chooseGlossary(glossary); setGlossaryForm({ name: glossary.name, description: glossary.description ?? "" }); }}>{glossary.name}</button>)}</div>
          <div className="mt-4 flex justify-between text-xs text-[#68687a]"><button disabled={glossaryPage <= 1} onClick={() => loadGlossaries(glossaryPage - 1)}>前へ</button><span>{glossaryPage}</span><button disabled={!glossaryHasNext} onClick={() => loadGlossaries(glossaryPage + 1)}>次へ</button></div>
        </section>

        <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-center justify-between"><h2 className="font-semibold">用語集詳細</h2>{selectedGlossary && <button className="text-sm text-red-700" onClick={removeGlossary}>削除</button>}</div>
          <form className="mt-4 space-y-3" onSubmit={saveGlossary}><label className="block text-sm font-medium">名前<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={glossaryForm.name} onChange={(event) => setGlossaryForm({ ...glossaryForm, name: event.target.value })} required /></label><label className="block text-sm font-medium">説明<textarea className="mt-1 min-h-28 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={glossaryForm.description} onChange={(event) => setGlossaryForm({ ...glossaryForm, description: event.target.value })} /></label><button className="w-full rounded-xl bg-[#6f70a4] px-4 py-2.5 text-sm font-semibold text-white">{selectedGlossary ? "更新" : "作成"}</button></form>
          <div className="mt-6 border-t border-[#9b9bc7]/20 pt-4"><p className="text-sm font-semibold">JSONインポート</p><select className="mt-2 w-full rounded-xl border border-[#d7d7e6] px-3 py-2 text-sm" value={importTarget} onChange={(event) => setImportTarget(event.target.value)}><option value="">新しい用語集</option>{glossaries.map((glossary) => <option key={glossary.glossaryId} value={glossary.glossaryId}>{glossary.name}</option>)}</select>{!importTarget && <><input className="mt-2 w-full rounded-xl border border-[#d7d7e6] px-3 py-2 text-sm" placeholder="新しい用語集名" value={importName} onChange={(event) => setImportName(event.target.value)} /><input className="mt-2 w-full rounded-xl border border-[#d7d7e6] px-3 py-2 text-sm" placeholder="説明（任意）" value={importDescription} onChange={(event) => setImportDescription(event.target.value)} /></>}
            <label className="mt-2 block cursor-pointer rounded-xl border border-dashed border-[#9b9bc7] px-3 py-2 text-center text-sm text-[#6f70a4]">JSONファイルを選択<input className="sr-only" type="file" accept="application/json,.json" onChange={importFile} /></label>
          </div>
        </section>

        <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-center justify-between"><h2 className="font-semibold">用語一覧</h2>{selectedGlossary && <button className="text-sm text-[#6f70a4]" onClick={() => { setSelectedTerm(null); setTermForm(emptyTerm); }}>新規</button>}</div>
          <div className="mt-3 space-y-2">{terms.map((term) => <button className={`block w-full rounded-xl p-3 text-left text-sm ${selectedTerm?.termId === term.termId ? "bg-[#ececf7] ring-1 ring-[#9b9bc7]" : "bg-[#f7f7fa] hover:bg-[#ececf7]"}`} key={term.termId} onClick={() => { setSelectedTerm(term); setTermForm({ sourceLanguage: term.sourceLanguage, targetLanguage: term.targetLanguage, sourceTerm: term.sourceTerm, targetTerm: term.targetTerm, description: term.description ?? "" }); }}>{term.sourceTerm} → {term.targetTerm}</button>)}</div>
          <div className="mt-4 flex justify-between text-xs text-[#68687a]"><button disabled={termPage <= 1 || !selectedGlossary} onClick={() => selectedGlossary && loadTerms(selectedGlossary.glossaryId, termPage - 1)}>前へ</button><span>{termPage}</span><button disabled={!termHasNext || !selectedGlossary} onClick={() => selectedGlossary && loadTerms(selectedGlossary.glossaryId, termPage + 1)}>次へ</button></div>
        </section>

        <section className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-[#9b9bc7]/20">
          <div className="flex items-center justify-between"><h2 className="font-semibold">用語詳細</h2>{selectedTerm && <button className="text-sm text-red-700" onClick={removeTerm}>削除</button>}</div>
          <form className="mt-4 space-y-3" onSubmit={saveTerm}><label className="block text-sm font-medium">原語言語<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={termForm.sourceLanguage} onChange={(event) => setTermForm({ ...termForm, sourceLanguage: event.target.value })} required /></label><label className="block text-sm font-medium">訳語言語<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={termForm.targetLanguage} onChange={(event) => setTermForm({ ...termForm, targetLanguage: event.target.value })} required /></label><label className="block text-sm font-medium">原語<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={termForm.sourceTerm} onChange={(event) => setTermForm({ ...termForm, sourceTerm: event.target.value })} required /></label><label className="block text-sm font-medium">訳語<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={termForm.targetTerm} onChange={(event) => setTermForm({ ...termForm, targetTerm: event.target.value })} required /></label><label className="block text-sm font-medium">説明<textarea className="mt-1 min-h-20 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={termForm.description} onChange={(event) => setTermForm({ ...termForm, description: event.target.value })} /></label><button className="w-full rounded-xl bg-[#6f70a4] px-4 py-2.5 text-sm font-semibold text-white" disabled={!selectedGlossary}>{selectedTerm ? "更新" : "作成"}</button></form>
        </section>
      </div>
    </AppShell>
  );
}
