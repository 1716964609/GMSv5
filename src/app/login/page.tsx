import Link from "next/link";

type LoginPageProps = {
  searchParams?: Promise<{ authError?: string | string[] }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const query = await searchParams;
  const hasAuthError = query?.authError === "1";

  return (
    <main className="grid min-h-screen place-items-center bg-[#f7f7fa] px-6 py-12 text-[#30303c]">
      <section className="w-full max-w-lg rounded-3xl bg-white p-8 shadow-sm ring-1 ring-[#9b9bc7]/25 sm:p-10">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#6f70a4]">GMS v5</p>
        <h1 className="mt-3 text-3xl font-semibold">ログイン</h1>
        {hasAuthError && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">認証を完了できませんでした。安全のため認証情報を確認できません。もう一度ログインするか、新規登録を開始してください。</p>}
        <p className="mt-4 leading-7 text-[#68687a]">メールアドレスとパスワードの認証は、設定済みのCognito Hosted UIで実行します。</p>
        <a className="mt-8 block rounded-2xl bg-[#6f70a4] px-5 py-3 text-center font-semibold text-white hover:bg-[#5d5e91]" href="/api/auth/login">Cognitoでログイン</a>
        <div className="mt-5 flex flex-wrap justify-between gap-3 text-sm">
          <a className="text-[#6f70a4] hover:underline" href="/api/auth/password-reset">パスワードを忘れた場合</a>
          <Link className="text-[#6f70a4] hover:underline" href="/register">新規登録</Link>
        </div>
        <Link className="mt-8 inline-block text-sm text-[#68687a] hover:underline" href="/">公開ページへ戻る</Link>
      </section>
    </main>
  );
}
