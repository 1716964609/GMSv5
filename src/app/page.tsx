import type { Metadata } from "next";
import Link from "next/link";

import styles from "./page.module.css";
import { LandingVisual } from "./landing-visual";

export const metadata: Metadata = {
  title: "専門用語の翻訳を、もっと思いどおりに。｜GMS",
  description:
    "用語集を管理し、翻訳に使う用語を選べる専門用語の翻訳支援サービスです。DeepLとOpenAIを組み合わせて翻訳作業をサポートします。",
};

const navItems = [
  ["機能", "#features"],
  ["使い方", "#how-it-works"],
  ["プライバシー", "#privacy"],
  ["よくある質問", "#faq"],
] as const;

const faqs = [
  {
    question: "GMSでは何ができますか？",
    answer:
      "ご自身の用語集を作成・管理し、翻訳に使う用語を選べます。同じ原語に複数の訳語候補がある場合は、翻訳前に候補を確認して選択できます。",
  },
  {
    question: "用語集や用語を登録できますか？",
    answer:
      "用語集と用語を登録・編集でき、対応するJSONファイルから用語をインポートできます。",
  },
  {
    question: "翻訳には何が必要ですか？",
    answer:
      "翻訳機能を利用するには、ご自身のDeepL APIキーとOpenAI APIキーを設定してください。各キーは保護された状態で保存され、画面に平文では表示されません。",
  },
  {
    question: "翻訳時にどのデータが外部サービスへ送られますか？",
    answer:
      "翻訳時、同意した原文と選択用語がDeepLへ送信されます。用語を選択した場合、DeepLで一時用語集を作成し、翻訳後に削除を要求します。OpenAIへは原文、中間訳、選択用語が送信されます。外部サービス側での処理・保持は各サービスの説明をご確認ください。",
  },
  {
    question: "翻訳内容はGMSに保存されますか？",
    answer:
      "原文、中間訳、最終訳文はGMSの業務データベースへ永続保存しません。DeepL・OpenAI側でのデータの取扱いをGMSが保証するものではありません。",
  },
  {
    question: "利用料金はいくらですか？",
    answer:
      "GMSは無料でご利用いただけます。翻訳に使用するDeepL・OpenAIのAPI利用料金は、各サービスの契約条件に従います。",
  },
];

function BookIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 48 48" fill="none">
      <path d="M24 13c-5-4-11-4-17-2v23c6-2 12-2 17 2m0-23c5-4 11-4 17-2v23c-6-2-12-2-17 2m0-23v23" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 48 48" fill="none">
      <rect x="9" y="9" width="30" height="30" rx="7" />
      <path d="m17 24 5 5 10-11" />
    </svg>
  );
}

function LayersIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 48 48" fill="none">
      <path d="m24 8 17 9-17 9-17-9 17-9Zm-17 17 17 9 17-9M7 33l17 9 17-9" />
    </svg>
  );
}

export default function Home() {
  return (
    <div className={styles.landingPage}>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <Link href="/" className={styles.brand} aria-label="GMS ホーム">
            <span className={styles.brandMark} aria-hidden="true">G</span>
            <span className={styles.brandName}>GMS</span>
          </Link>
          <nav className={styles.primaryNav} aria-label="ページ内ナビゲーション">
            {navItems.map(([label, href]) => (
              <a href={href} key={href}>{label}</a>
            ))}
          </nav>
          <div className={styles.authNav}>
            <Link className={styles.loginLink} href="/login">ログイン</Link>
            <Link className={styles.headerCta} href="/register">新規登録</Link>
          </div>
        </div>
      </header>

      <a className={styles.skipLink} href="#main-content">本文へスキップ</a>
      <main id="main-content" tabIndex={-1}>
        <section className={styles.hero} aria-labelledby="hero-title">
          <svg className={styles.heroWaves} viewBox="0 0 1440 180" preserveAspectRatio="none" aria-hidden="true">
            <path d="M0 32C190 90 210 190 560 165S1100 190 1440 40V180H0Z" fill="#ad79ee" fillOpacity=".5" />
            <path d="M0 120C180 10 330 210 710 155S1160 45 1440 112V180H0Z" fill="#dcc5ff" fillOpacity=".58" />
          </svg>
          <div className={styles.heroInner}>
            <div className={styles.heroCopy}>
              <p className={styles.eyebrow}>専門用語の管理・翻訳支援</p>
              <h1 id="hero-title">
                <span className={styles.heroTitleLine}>専門用語の翻訳を、</span>
                <span className={styles.heroTitleLine}>もっと思いどおりに。</span>
              </h1>
              <p className={styles.heroDescription}>
                ご自身の用語集を管理し、翻訳に使う用語を選べます。DeepLで一次翻訳し、OpenAIで訳文を整えることで、専門分野の翻訳作業をサポートします。
              </p>
              <div className={styles.heroActions}>
                <Link className={styles.primaryButton} href="/register">新規登録 <span aria-hidden="true">→</span></Link>
                <Link className={styles.secondaryButton} href="/login">ログイン</Link>
              </div>
              <p className={styles.heroNote}>GMSは無料でご利用いただけます。API利用料金は各サービスの契約条件に従います。</p>
              <ul className={styles.heroHighlights} aria-label="主な機能">
                <li><BookIcon /><span>用語集を管理</span></li>
                <li><CheckIcon /><span>使う用語を選択</span></li>
                <li><LayersIcon /><span>訳語候補を確認</span></li>
              </ul>
            </div>
            <LandingVisual />
          </div>
        </section>

        <section className={styles.featuresSection} id="features" aria-labelledby="features-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>FEATURES</p>
            <h2 id="features-title">翻訳の前後を、用語集で支える。</h2>
            <p>用語を整え、必要な用語を選び、訳語候補を確認できます。</p>
          </div>
          <div className={styles.featureGrid}>
            <article className={styles.featureCard}>
              <span className={styles.featureIcon}><BookIcon /></span>
              <h3>用語をまとめて管理</h3>
              <p>専門分野の用語を登録・検索。対応するJSONファイルからインポートできます。</p>
            </article>
            <article className={styles.featureCard}>
              <span className={styles.featureIcon}><CheckIcon /></span>
              <h3>翻訳に使う用語を選択</h3>
              <p>用語集から必要な用語を選び、その翻訳リクエストに適用できます。</p>
            </article>
            <article className={styles.featureCard}>
              <span className={styles.featureIcon}><LayersIcon /></span>
              <h3>訳語候補を確認</h3>
              <p>同じ原語に複数の訳語がある場合は、翻訳前に候補を選べます。</p>
            </article>
          </div>

        </section>

        <section className={styles.stepsSection} id="how-it-works" aria-labelledby="steps-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>HOW IT WORKS</p>
            <h2 id="steps-title">ご利用の流れ</h2>
          </div>
          <ol className={styles.stepsGrid}>
            <li><span className={styles.stepNumber}>01</span><h3>アカウントを登録</h3><p>登録後、メールアドレスを確認して利用を開始します。</p></li>
            <li><span className={styles.stepNumber}>02</span><h3>APIキーを設定</h3><p>ご自身のDeepL・OpenAI APIキーを設定します。</p></li>
            <li><span className={styles.stepNumber}>03</span><h3>用語を選んで翻訳</h3><p>使う用語を選び、翻訳を実行して結果を確認します。</p></li>
          </ol>
        </section>

        <section className={styles.privacySection} id="privacy" aria-labelledby="privacy-title">
          <div className={styles.privacyIntro}>
            <p className={styles.eyebrow}>PRIVACY</p>
            <h2 id="privacy-title">データの取り扱いを、わかりやすく。</h2>
            <p>翻訳時にGMS内で扱う情報と、外部サービスへ送信する情報を分けてご案内します。</p>
          </div>
          <div className={styles.privacyDetails}>
            <ul>
              <li>翻訳時、同意した原文と選択用語をDeepLへ送信します。選択用語がある場合は一時用語集を作成し、翻訳後に削除を要求します。</li>
              <li>OpenAIへは原文、中間訳、選択用語を送信します。</li>
              <li>原文、中間訳、最終訳文はGMSの業務データベースへ永続保存しません。</li>
              <li>APIキーは保護された状態で保存し、画面に平文では表示しません。外部サービス側の処理・保持をGMSが保証するものではありません。</li>
            </ul>
            <div className={styles.privacyLinks}>
              <a href="https://www.deepl.com/en/privacy" target="_blank" rel="noreferrer">DeepLのプライバシー情報 <span aria-hidden="true">↗</span></a>
              <a href="https://developers.openai.com/api/docs/guides/your-data" target="_blank" rel="noreferrer">OpenAI APIのデータ管理 <span aria-hidden="true">↗</span></a>
            </div>
          </div>
        </section>

        <section className={styles.faqSection} id="faq" aria-labelledby="faq-title">
          <div className={styles.sectionHeading}>
            <p className={styles.eyebrow}>FAQ</p>
            <h2 id="faq-title">よくある質問</h2>
          </div>
          <div className={styles.faqList}>
            {faqs.map(({ question, answer }) => (
              <details className={styles.faqItem} key={question}>
                <summary>{question}</summary>
                <p>{answer}</p>
              </details>
            ))}
          </div>
        </section>

        <section className={styles.finalCta} aria-labelledby="final-cta-title">
          <div>
            <p className={styles.eyebrow}>GET STARTED</p>
            <h2 id="final-cta-title">専門用語を活かした翻訳を、ここから。</h2>
            <p>GMSの利用は無料です。DeepL・OpenAI API料金は各サービスの契約条件に従います。</p>
          </div>
          <div className={styles.finalActions}>
            <Link className={styles.lightButton} href="/register">新規登録 <span aria-hidden="true">→</span></Link>
            <Link className={styles.outlineLightButton} href="/login">ログイン</Link>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <Link href="/" className={styles.footerBrand}>GMS</Link>
        <p>専門用語の管理・翻訳支援</p>
        <div className={styles.footerLinks}><a href="#privacy">プライバシー</a><a href="mailto:HXLJ9909@Outlook.jp">お問い合わせ：HXLJ9909@Outlook.jp</a></div>
        <small>© {new Date().getFullYear()} GMS</small>
      </footer>
    </div>
  );
}
