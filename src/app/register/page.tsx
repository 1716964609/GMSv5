import Link from "next/link";

export default function RegisterPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#f7f7fa] px-6 py-12 text-[#30303c]">
      <section className="w-full max-w-lg rounded-3xl bg-white p-8 shadow-sm ring-1 ring-[#9b9bc7]/25 sm:p-10">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#6f70a4]">GMS v5</p>
        <h1 className="mt-3 text-3xl font-semibold">新規アカウント登録</h1>
        <p className="mt-4 leading-7 text-[#68687a]">メール確認、有効期限、再送制限などはCognitoの設定済みポリシーに従います。</p>
        <a className="mt-8 block rounded-2xl bg-[#6f70a4] px-5 py-3 text-center font-semibold text-white hover:bg-[#5d5e91]" href="/api/auth/register">Cognitoで登録を開始</a>
        <div className="mt-6 flex justify-between gap-3 text-sm">
          <Link className="text-[#6f70a4] hover:underline" href="/login">ログイン</Link>
          <Link className="text-[#68687a] hover:underline" href="/">公開ページへ戻る</Link>
        </div>
      </section>
    </main>
  );
}
