export type NarrativePhraseCategory = "negative" | "positive" | "buzz";

export type NarrativePhraseTemplate = {
  term: string;
  weight: number;
  description: string;
};

export type TeamPhraseDefinition = NarrativePhraseTemplate & {
  phrase: string;
  team: string;
  category: NarrativePhraseCategory;
};

export const PHRASE_CATEGORY_DESCRIPTIONS: Record<NarrativePhraseCategory, string> = {
  negative: "Negative / risk phrases",
  positive: "Positive / momentum phrases",
  buzz: "Buzz / volatility phrases",
};

export const NARRATIVE_PHRASE_GROUPS: Record<NarrativePhraseCategory, NarrativePhraseTemplate[]> = {
  negative: [
    { term: "injury", weight: -2.0, description: "Potential health concern" },
    { term: "questionable", weight: -2.0, description: "Availability uncertainty" },
    { term: "suspended", weight: -3.0, description: "Potential absence or discipline" },
    { term: "slump", weight: -1.5, description: "Performance dip" },
    { term: "short rest", weight: -1.0, description: "Compressed schedule" },
    { term: "travel", weight: -1.0, description: "Travel or fatigue narrative" },
  ],
  positive: [
    { term: "returning", weight: 2.0, description: "Player or form returning" },
    { term: "healthy", weight: 2.0, description: "Positive health update" },
    { term: "hot streak", weight: 1.5, description: "Strong recent form" },
    { term: "momentum", weight: 1.5, description: "Positive narrative momentum" },
    { term: "breakout", weight: 1.0, description: "Emerging upside" },
    { term: "dominant", weight: 1.0, description: "Strong dominant buzz" },
  ],
  buzz: [
    { term: "upset", weight: 0, description: "Upset chatter" },
    { term: "odds", weight: 0, description: "Odds-related buzz" },
    { term: "prediction", weight: 0, description: "Prediction chatter" },
    { term: "cinderella", weight: 0, description: "Tournament-style buzz" },
  ],
};

export const buildTeamPhrases = (teamName: string): TeamPhraseDefinition[] => {
  const phrases: TeamPhraseDefinition[] = [];
  (Object.keys(NARRATIVE_PHRASE_GROUPS) as NarrativePhraseCategory[]).forEach((category) => {
    NARRATIVE_PHRASE_GROUPS[category].forEach((template) => {
      phrases.push({
        ...template,
        category,
        team: teamName,
        phrase: `${teamName} ${template.term}`,
      });
    });
  });
  return phrases;
};
