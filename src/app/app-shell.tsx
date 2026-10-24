"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

export function AppShell({ children, title }: { children: ReactNode; title: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [authState, setAuthState] = useState<"checking" | "authorized" | "error">("checking");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((response) => {
        if (response.status === 401) {
          router.replace("/login");
          return;
        }
        if (!response.ok) {
          throw new Error("Authentication state could not be verified");
        }
        if (!cancelled) setAuthState("authorized");
      })
      .catch(() => {
        if (!cancelled) setAuthState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (authState === "checking") {
    return <main className="grid min-h-screen place-items-center bg-[#f7f7fa] p-6 text-sm text-[#68687a]">認証状態を確認しています…</main>;
  }

  if (authState === "error") {
    return <main className="grid min-h-screen place-items-center bg-[#f7f7fa] p-6 text-sm text-red-700">認証状態を確認できません。時間をおいて再試行してください。</main>;
  }

  return (
    <div className="min-h-screen bg-[#f7f7fa] text-[#30303c]">
      <header className="flex items-center justify-between border-b border-[#9b9bc7]/20 bg-white px-4 py-4 shadow-sm sm:px-8">
        <div className="flex items-center gap-3">
          <button className="grid h-10 w-10 place-items-center rounded-xl border border-[#9b9bc7]/30 text-xl" onClick={() => setOpen(!open)} aria-label="メニュー"><span aria-hidden="true">☰</span></button>
          <Link className="font-semibold" href="/glossary">GMS v5</Link>
          <span className="hidden text-sm text-[#68687a] sm:inline">/ {title}</span>
        </div>
        <form action="/api/auth/logout" method="post">
          <button
            type="submit"
            className="rounded-full border border-[#9b9bc7]/40 px-4 py-2 text-sm font-semibold text-[#565667] hover:bg-[#f7f7fa]"
          >
            ログアウト
          </button>
        </form>
      </header>
      <div className="relative flex">
        {open && (
          <aside className="absolute z-10 min-h-[calc(100vh-73px)] w-64 border-r border-[#9b9bc7]/20 bg-white p-5 shadow-lg sm:static sm:shadow-none">
            <nav className="space-y-2" onClick={() => setOpen(false)}>
              <Link className="block rounded-xl px-4 py-3 font-medium hover:bg-[#f7f7fa]" href="/glossary">Glossary Management</Link>
              <Link className="block rounded-xl px-4 py-3 font-medium hover:bg-[#f7f7fa]" href="/translation">BYO Translation</Link>
              <div className="my-4 border-t border-[#9b9bc7]/20" />
              <Link className="block rounded-xl px-4 py-3 font-medium hover:bg-[#f7f7fa]" href="/settings">Settings</Link>
            </nav>
          </aside>
        )}
        <main className="min-w-0 flex-1 p-4 sm:p-8">{children}</main>
      </div>
    </div>
  );
}
