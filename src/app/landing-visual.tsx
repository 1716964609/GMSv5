import styles from "./page.module.css";

/** Decorative feature illustration; deliberately contains no interactive controls. */
export function LandingVisual() {
  return (
    <figure className={styles.heroVisual}>
      <div className={styles.visualScene} aria-hidden="true">
        <div className={styles.sceneGlow} />
        <svg className={styles.lightTrail} viewBox="0 0 800 560" fill="none">
          <defs>
            <linearGradient id="orbit-light" x1="40" y1="440" x2="740" y2="130" gradientUnits="userSpaceOnUse">
              <stop stopColor="#b598ff" stopOpacity="0" />
              <stop offset=".35" stopColor="#efc6ff" />
              <stop offset=".66" stopColor="white" />
              <stop offset="1" stopColor="#e4b5ff" stopOpacity="0" />
            </linearGradient>
          </defs>
          <ellipse cx="405" cy="315" rx="375" ry="137" transform="rotate(-18 405 315)" stroke="url(#orbit-light)" strokeWidth="2.5" />
        </svg>
        <div className={styles.glassBack} />
        <div className={styles.glassMiddle} />
        <div className={styles.glassPanel}>
          <div className={styles.panelBar}>
            <span className={styles.panelBrand}>G</span><strong>GMS</strong>
            <span className={styles.panelBarLabel}>Glossary & Translation</span>
            <span className={styles.panelDots}>•••</span>
          </div>
          <div className={styles.panelContent}>
            <div className={styles.panelHeading}><span>あなたの用語を、翻訳の力に。</span><small>CONCEPT</small></div>
            <div className={styles.panelColumns}>
              <div className={styles.conceptGlossaries}>
                <p>用語をまとめる</p>
                <div className={styles.conceptSelected}><span>01</span>臨床試験・治験</div>
                <div><span>02</span>医薬品安全性</div>
                <div><span>03</span>添付文書・薬理</div>
                <small>自分の用語集を、ひとつに。</small>
              </div>
              <div className={styles.conceptTerms}>
                <p>必要な用語を選ぶ <span>JA → EN</span></p>
                <div><i>✓</i><span>無作為化比較試験<small>Randomized controlled trial</small></span></div>
                <div><i>✓</i><span>主要評価項目<small>Primary endpoint</small></span></div>
                <div><i>✓</i><span>有害事象<small>Adverse event</small></span></div>
                <div className={styles.conceptFlow}>用語を管理 <b>→</b> 選んで翻訳</div>
              </div>
            </div>
          </div>
        </div>
        <span className={styles.sceneSparkle} />
      </div>
    </figure>
  );
}
