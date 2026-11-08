> CR-02方針変更（2026年11月8日）：実画面画像の撮影・掲載は中止しました。このデータと以下の手順は将来の任意デモ用です。現在のLP実装・レビューに合成DB準備や撮影は必要ありません。

# CR-02 public landing demo data

These three JSON files contain synthetic Japanese-to-English sample Terms for the approved landing-page scenario. They use the import document shape accepted by the Glossary Management JSON import flow (`{ "terms": [...] }`) and have been verified through the existing import service against a disposable SQLite database.

Import each file only into a dedicated local demo GMS database, creating the corresponding glossary:

1. `clinical-trials.json` → 臨床試験・治験用語集
2. `pharmacovigilance.json` → 医薬品安全性・有害事象用語集
3. `labeling-pharmacology.json` → 医薬品添付文書・薬理用語集

Do not import these Terms into production. Do not copy or use the existing `.data/gmsv5.sqlite` or any production database for screenshots. These Terms are synthetic examples; no translation output is represented as having been generated. If the LP later displays translation output, generate it through the running application using a synthetic example sentence and a separately authorized local provider setup.

## Platform Team: isolated local screenshot procedure

### 1. Start with a new disposable SQLite file

From the repository root, create a new temporary directory and explicitly point GMS at a database file inside it. Do not omit `GMS_DATABASE_PATH`: without it, development defaults to `.data/gmsv5.sqlite`.

```sh
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gmsv5-cr02-demo.XXXXXX")"
export GMS_DATABASE_PATH="$DEMO_DIR/gmsv5.sqlite"
npm run dev -- --hostname localhost --port 3000
```

Keep the printed `DEMO_DIR` value for this capture session. Next.js initializes schema in the new file when GMS first needs the database. Stop the dev server when captures are complete. The temp database can then be retained until the images are reviewed and removed by its exact path.

### 2. Use the existing authentication flow safely

- Use an approved, dedicated **non-production Cognito demo account**. Do not sign in with a real user's personal account: the existing OAuth callback stores the account subject, email, and display name in the configured SQLite file.
- Configure the local non-production Cognito client with the exact local callback/logout URLs used for the server (`http://localhost:3000/api/auth/callback` and `http://localhost:3000/`). Do not change auth code or bypass OAuth/PKCE. If only a production Cognito client/account is available or local callbacks are not approved, pause and ask Platform Team to prepare an approved non-production login setup.
- Use a sanitized local environment containing only the non-production Cognito configuration. Do not print or commit `.env.local`; do not place production database paths or production credentials in the local environment.
- No DeepL or OpenAI API keys are needed to capture these screens. Do not translate text during capture.

### 3. Import the synthetic glossaries

After signing in to the local app, open `http://localhost:3000/glossary`. In the Glossary Management JSON import area, leave the target as a new glossary, enter the glossary name shown above, choose the matching file under `scripts/data/landing-demo/`, and import it. Repeat for all three files. Confirm each glossary appears and its Terms are visible. Do not import the data into any other environment.

### 4. Capture the two actual product screens

Use a desktop browser viewport around 1440 × 1000 or larger, hide browser developer tools, and keep only synthetic data visible. Save the browser's actual page screenshot under `public/images/landing/` (create this directory if needed). Do not redraw or compose a mock UI.

1. **Hero image:** Open `/glossary`, select **臨床試験・治験用語集** in the glossary list, and wait for the real term list/detail panels to settle. Capture the GMS page with the selected glossary and its sample Terms visible as `public/images/landing/glossary-management-clinical.png`.
2. **Features image:** Open `/translation`, expand **医薬品安全性・有害事象用語集**, then select multiple visible Terms such as **有害事象**, **重篤な有害事象**, and **蕁麻疹**. Confirm the selected count and checkboxes are visible. Leave the source text empty and do not press Translate. Capture the actual GMS page as `public/images/landing/byo-translation-pharmacovigilance.png`.

Before delivery, visually inspect each image for personal/contact data, credentials, browser notifications, real user data, or non-synthetic Terms. Keep the third glossary available in the local demo database but do not add it to the screenshots unless a later review asks for it. The screenshot files are not included in this repository yet; this procedure does not mean they have been captured.
