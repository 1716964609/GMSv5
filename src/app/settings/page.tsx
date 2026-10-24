"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import type { CredentialSummary } from "@/lib/domain/types";
import { AppShell } from "../app-shell";

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, headers: { "content-type": "application/json", ...(options?.headers ?? {}) } });
  const payload = (await response.json().catch(() => ({}))) as T & { error?: { message?: string } };
  if (!response.ok) throw new Error(payload.error?.message ?? "設定の更新に失敗しました");
  return payload;
}

export default function SettingsPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [provider, setProvider] = useState<"DEEPL" | "OPENAI">("DEEPL");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const [profile, credentialResult] = await Promise.all([
      api<{ user: { email: string; displayName: string | null } }>("/api/profile"),
      api<{ credentials: CredentialSummary[] }>("/api/credentials"),
    ]);
    setEmail(profile.user.email);
    setDisplayName(profile.user.displayName ?? "");
    setCredentials(credentialResult.credentials);
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      load().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "設定を読み込めませんでした"));
    }, 0);
    return () => window.clearTimeout(timer);
    // The initial request intentionally runs once when the screen opens.
  }, []);

  async function saveProfile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api("/api/profile", { method: "PATCH", body: JSON.stringify({ displayName }) });
      setMessage("プロフィールを更新しました");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "プロフィール更新に失敗しました"); }
  }

  async function saveCredential(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api("/api/credentials", { method: "POST", body: JSON.stringify({ provider, apiKey }) });
      setApiKey("");
      await load();
      setMessage(`${provider} APIキーを保存しました。平文は再表示されません。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "APIキー保存に失敗しました"); }
  }

  async function removeCredential(selectedProvider: "DEEPL" | "OPENAI") {
    try {
      await api(`/api/credentials/${selectedProvider}`, { method: "DELETE" });
      await load();
      setMessage(`${selectedProvider} APIキーを削除しました`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "APIキー削除に失敗しました"); }
  }

  async function deleteAccount(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!deleteConfirmed || !window.confirm("30日内部保持後に恒久削除され、復旧できません。続行しますか？")) return;
    try {
      const result = await api<{ deletionScheduledAt: string }>("/api/account/delete", { method: "POST", body: JSON.stringify({ currentPassword, confirmed: true }) });
      setMessage(`削除を確定しました。恒久削除予定: ${result.deletionScheduledAt}`);
      router.push("/");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "アカウント削除に失敗しました"); }
  }

  async function changePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api("/api/auth/password-change", { method: "POST", body: JSON.stringify({ currentPassword, newPassword }) });
      setCurrentPassword("");
      setNewPassword("");
      setMessage("パスワードを変更しました");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "パスワード変更に失敗しました"); }
  }

  return (
    <AppShell title="Settings">
      <div className="mb-5"><p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#6f70a4]">UC-014〜UC-016</p><h1 className="mt-1 text-3xl font-semibold">Settings</h1><p className="mt-2 text-sm text-[#68687a]">{message || "メールアドレスは変更できません。"}</p>{error && <p className="mt-2 text-sm text-red-700">{error}</p>}</div>
      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20"><h2 className="text-xl font-semibold">プロフィール</h2><form className="mt-4 space-y-4" onSubmit={saveProfile}><label className="block text-sm font-medium">メールアドレス<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] bg-[#f7f7fa] px-3 py-2 text-[#68687a]" value={email} readOnly /></label><label className="block text-sm font-medium">表示名<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><button className="rounded-xl bg-[#6f70a4] px-4 py-2.5 text-sm font-semibold text-white">表示名を保存</button></form><div className="mt-6 border-t border-[#9b9bc7]/20 pt-5"><h3 className="font-semibold">パスワード</h3><p className="mt-2 text-sm leading-6 text-[#68687a]">パスワードはCognitoが管理し、GMSは平文やハッシュを保存しません。</p><form className="mt-3 space-y-3" onSubmit={changePassword}><input className="w-full rounded-xl border border-[#d7d7e6] px-3 py-2" type="password" placeholder="現在のパスワード" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required autoComplete="current-password" /><input className="w-full rounded-xl border border-[#d7d7e6] px-3 py-2" type="password" placeholder="新しいパスワード" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required autoComplete="new-password" /><button className="rounded-xl border border-[#9b9bc7]/40 px-4 py-2 text-sm font-semibold text-[#565667]">パスワードを変更</button></form></div></section>

        <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-[#9b9bc7]/20"><h2 className="text-xl font-semibold">API認証情報</h2><p className="mt-2 text-sm leading-6 text-[#68687a]">DeepL・OpenAIのキーは暗号化保存し、画面へ平文を返しません。</p><div className="mt-4 space-y-2">{credentials.map((credential) => <div className="flex items-center justify-between rounded-xl bg-[#f7f7fa] p-3 text-sm" key={credential.provider}><span>{credential.provider} <span className="text-[#68687a]">（設定済み）</span></span><button className="text-red-700" onClick={() => removeCredential(credential.provider)}>削除</button></div>)}</div><form className="mt-5 space-y-3 border-t border-[#9b9bc7]/20 pt-5" onSubmit={saveCredential}><div className="grid grid-cols-2 gap-3"><label className="text-sm font-medium">Provider<select className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" value={provider} onChange={(event) => setProvider(event.target.value as "DEEPL" | "OPENAI")}><option value="DEEPL">DeepL</option><option value="OPENAI">OpenAI</option></select></label><label className="text-sm font-medium">APIキー<input className="mt-1 w-full rounded-xl border border-[#d7d7e6] px-3 py-2" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required autoComplete="off" /></label></div><button className="rounded-xl bg-[#6f70a4] px-4 py-2.5 text-sm font-semibold text-white">登録・置換</button></form></section>

        <section className="rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm lg:col-span-2"><h2 className="text-xl font-semibold text-red-900">アカウント削除</h2><p className="mt-2 max-w-3xl text-sm leading-7 text-red-900/80">確定すると通常ログインとサービス利用を直ちに停止し、APIキーを削除します。アカウント情報・Glossary・Termは30暦日内部保持後に恒久削除され、ユーザー向け復旧機能はありません。</p><form className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end" onSubmit={deleteAccount}><label className="text-sm font-medium text-red-900">現在のパスワード<input className="mt-1 w-full rounded-xl border border-red-200 bg-white px-3 py-2" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required autoComplete="current-password" /></label><label className="flex items-center gap-2 text-sm text-red-900"><input type="checkbox" checked={deleteConfirmed} onChange={(event) => setDeleteConfirmed(event.target.checked)} />最終確認</label><button className="rounded-xl bg-red-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50" disabled={!deleteConfirmed}>削除を確定</button></form></section>
      </div>
    </AppShell>
  );
}
