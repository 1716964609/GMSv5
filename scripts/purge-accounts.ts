import { runMigrations } from "../src/lib/db/migrations.ts";
import { purgeEligibleAccounts } from "../src/lib/db/repository.ts";
import { openDatabase } from "../src/lib/db/sqlite.ts";

const handle = openDatabase();

try {
  runMigrations(handle.db);
  const purged = purgeEligibleAccounts(handle.db);
  console.log(`Purged local accounts: ${purged.length}`);
} finally {
  handle.db.close();
}
