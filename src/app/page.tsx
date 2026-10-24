import Link from "next/link";

const profileUrl = process.env.NEXT_PUBLIC_BRAND_PROFILE_URL;

export default function Home() {
  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,#f7f7fa_0%,#ececf7_100%)] px-4 py-8 text-[#30303c] sm:px-8 sm:py-12">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-col gap-6 rounded-3xl bg-white/95 p-5 shadow-sm ring-1 ring-[#9b9bc7]/25 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <div className="flex items-center gap-4">
            <a
              className="grid h-12 w-12 place-items-center rounded-2xl bg-[#9b9bc7] text-xl font-bold text-white shadow-sm"
              href={profileUrl ?? "/"}
              aria-label="GMS v5 brand profile"
            >
              G
            </a>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-[#6f70a4]">GMS v5</p>
              <p className="text-lg font-semibold">Glossary Management System</p>
            </div>
          </div>
          <nav className="flex flex-wrap gap-3" aria-label="公開認証メニュー">
            <Link className="rounded-full border border-[#9b9bc7]/50 px-5 py-2 text-sm font-semibold text-[#565667] hover:bg-[#f7f7fa]" href="/login">ログイン</Link>
            <Link className="rounded-full bg-[#6f70a4] px-5 py-2 text-sm font-semibold text-white hover:bg-[#5d5e91]" href="/register">新規登録</Link>
          </nav>
        </header>

        <section className="mt-8 grid gap-8 rounded-3xl bg-white/95 p-6 shadow-sm ring-1 ring-[#9b9bc7]/25 sm:p-10 lg:grid-cols-2">
          <div className="space-y-7">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#6f70a4]">Bring Your Own Glossary</p>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight sm:text-5xl">専門用語を、あなたの翻訳資産に。</h1>
              <p className="mt-5 max-w-xl text-base leading-8 text-[#565667]">GMS v5は、ユーザー自身の用語集をDeepLとOpenAIの2段階翻訳へ活用するための用語管理システムです。</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                ["用語管理", "GlossaryとTermを現在状態として管理"],
                ["衝突解決", "翻訳前に候補をユーザー自身が選択"],
                ["BYO API Key", "外部キーは暗号化してユーザー単位で管理"],
              ].map(([title, description]) => (
                <div className="rounded-2xl bg-[#f7f7fa] p-4" key={title}>
                  <p className="font-semibold">{title}</p>
                  <p className="mt-2 text-sm leading-6 text-[#68687a]">{description}</p>
                </div>
              ))}
            </div>
          </div>

          <aside className="rounded-3xl bg-[#f7f7fa] p-6 sm:p-8">
            <h2 className="text-xl font-semibold">データ送信とプライバシー</h2>
            <div className="mt-5 space-y-4 text-sm leading-7 text-[#565667]">
              <p>翻訳実行時に、同意した原文と選択用語をユーザー自身のDeepL・OpenAI APIキーで外部送信します。</p>
              <p>原文、中間訳、最終訳文はGMSの業務データベースへ保存しません。</p>
              <p>Glossary、Term、プロフィール、API認証情報はログインユーザーの所有範囲に限定されます。</p>
            </div>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link className="rounded-full bg-[#6f70a4] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#5d5e91]" href="/register">はじめる</Link>
              <Link className="rounded-full border border-[#9b9bc7]/50 px-5 py-2.5 text-sm font-semibold text-[#565667] hover:bg-white" href="/login">既存ユーザーはこちら</Link>
            </div>
          </aside>
        </section>

        <footer className="px-3 py-8 text-center text-sm text-[#68687a]">© {new Date().getFullYear()} GMS v5 · Specification-driven glossary management</footer>
      </div>
    </main>
  );
}
