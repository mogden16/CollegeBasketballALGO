import { normalizeTeamName } from "./teamName";

export type TeamQueryProfile = {
  displayName: string;
  normalizedName: string;
  searchName: string;
  tokens: string[];
  matchupAliases: string[];
};

const STOPWORDS = new Set(["the", "university", "of"]);

export const buildTeamQueryProfile = (teamName: string): TeamQueryProfile => {
  const displayName = teamName.trim();
  const normalizedName = normalizeTeamName(displayName);
  const compactName = normalizedName.replace(/\s+/g, " ").trim();
  const tokens = compactName.split(" ").filter(Boolean);
  const reducedName = tokens.filter((token) => !STOPWORDS.has(token)).join(" ");
  const stateAlias = compactName.replace(/\bstate\b/g, "st").trim();
  const matchupAliases = [displayName, compactName, reducedName, stateAlias]
    .filter((value, index, arr) => value && arr.indexOf(value) === index);

  return {
    displayName,
    normalizedName,
    searchName: compactName,
    tokens,
    matchupAliases,
  };
};

export const buildMatchupQueries = (teamA: TeamQueryProfile, teamB: TeamQueryProfile): string[] => {
  const pairs = [
    `${teamA.searchName} ${teamB.searchName}`,
    `${teamA.searchName} vs ${teamB.searchName}`,
    `${teamA.searchName} ${teamB.searchName} matchup`,
  ];

  return pairs.filter((value, index, arr) => value && arr.indexOf(value) === index);
};
