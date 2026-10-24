/**
 * Canonical form stored in SQLite for Term source/target text.
 *
 * SQLite does not provide the required Unicode NFC normalization. All term
 * writes must pass through this function before the unique constraint is
 * evaluated.
 */
export function normalizeTermText(value: string): string {
  return value.trim().normalize("NFC");
}

export function normalizeTermPair(sourceTerm: string, targetTerm: string) {
  return {
    sourceTerm: normalizeTermText(sourceTerm),
    targetTerm: normalizeTermText(targetTerm),
  };
}
