export const normalizeTeamName = (input: string): string =>
  input
    .trim()
    .replace(/^\d+\s*/, "")
    .replace(/\s*\d+$/, "")
    .replace(/[’']/g, "")
    .replace(/\./g, "")
    .replace(/&/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();
