import { runMigrations } from "../src/lib/db/migrations.ts";
import { openDatabase } from "../src/lib/db/sqlite.ts";

const handle = openDatabase();

try {
  const result = runMigrations(handle.db);
  const applied = result.applied.map(({ version, name }) => `${version}:${name}`);
  const skipped = result.skipped.map(({ version, name }) => `${version}:${name}`);

  console.log(`Database: ${handle.path}`);
  console.log(`Applied migrations: ${applied.length > 0 ? applied.join(", ") : "none"}`);
  console.log(`Skipped migrations: ${skipped.length > 0 ? skipped.join(", ") : "none"}`);
} finally {
  handle.db.close();
}
