export default function Home() {
  return (
    <main className="min-h-screen bg-[#f7f7fa] px-6 py-16 text-[#30303c]">
      <section className="mx-auto flex min-h-[60vh] max-w-3xl flex-col justify-center rounded-3xl bg-white p-10 shadow-sm ring-1 ring-[#9b9bc7]/20 sm:p-16">
        <p className="mb-4 text-sm font-semibold uppercase tracking-[0.2em] text-[#6f70a4]">
          GMS v5
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Glossary Management System
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-8 text-[#565667]">
          Next.js App RouterのProject Scaffoldingが完了しました。仕様書とArchitecture文書に基づく業務機能は、後続のAIUP工程で実装します。
        </p>
        <div className="mt-10 rounded-2xl bg-[#f7f7fa] p-5 text-sm text-[#565667]">
          <p className="font-medium text-[#30303c]">現在の状態</p>
          <p className="mt-2">Scaffolding only — database、認証、AWS、業務Use Caseは未実装です。</p>
        </div>
      </section>
    </main>
  );
}
