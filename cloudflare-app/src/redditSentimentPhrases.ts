export type SentimentCategory = "negative" | "positive" | "buzz";

export type PhraseDefinition = {
  phrase: string;
  weight: number;
  aliases?: string[];
};

export const REDDIT_SENTIMENT_SUBREDDITS = ["CollegeBasketball"] as const;
export const REDDIT_SENTMENT_CACHE_TTL_MS = 30 * 60 * 1000;
export const REDDIT_LOW_SIGNAL_SAMPLE_THRESHOLD = 4;

export const REDDIT_SENTIMENT_PHRASES: Record<SentimentCategory, PhraseDefinition[]> = {
  negative: [
    { phrase: "injury", weight: -2.5, aliases: ["injuries", "injured"] },
    { phrase: "out", weight: -2.75 },
    { phrase: "questionable", weight: -1.75 },
    { phrase: "doubtful", weight: -2.5 },
    { phrase: "suspended", weight: -2.75, aliases: ["suspension"] },
    { phrase: "illness", weight: -1.75, aliases: ["sick"] },
    { phrase: "slump", weight: -1.25, aliases: ["slumping"] },
    { phrase: "struggling", weight: -1.25, aliases: ["struggle"] },
    { phrase: "cold shooting", weight: -1.25 },
    { phrase: "foul trouble", weight: -0.75 },
    { phrase: "turnover problems", weight: -1.25, aliases: ["turnovers", "turnover issue"] },
    { phrase: "tired", weight: -0.75, aliases: ["fatigued"] },
    { phrase: "travel", weight: -0.75, aliases: ["road trip"] },
    { phrase: "short rest", weight: -0.75 }
  ],
  positive: [
    { phrase: "healthy", weight: 2 },
    { phrase: "returning", weight: 2, aliases: ["returns", "back in lineup"] },
    { phrase: "starter returns", weight: 2 },
    { phrase: "hot streak", weight: 1.5, aliases: ["heating up", "hot"] },
    { phrase: "momentum", weight: 1.5 },
    { phrase: "breakout", weight: 1 },
    { phrase: "dominant", weight: 1 },
    { phrase: "depth", weight: 0.75, aliases: ["deep bench"] },
    { phrase: "defense", weight: 0.75, aliases: ["defensive"] },
    { phrase: "rebounding", weight: 0.75, aliases: ["boards"] },
    { phrase: "locked in", weight: 1.5 },
    { phrase: "confident", weight: 1, aliases: ["confidence"] },
    { phrase: "rolling", weight: 1.5 }
  ],
  buzz: [
    { phrase: "upset", weight: 1, aliases: ["upset alert"] },
    { phrase: "sleeper", weight: 1 },
    { phrase: "fraud", weight: 1, aliases: ["frauds"] },
    { phrase: "trap game", weight: 1 },
    { phrase: "cinderella", weight: 1 },
    { phrase: "bracket", weight: 1, aliases: ["march madness"] },
    { phrase: "revenge game", weight: 1 },
    { phrase: "must win", weight: 1 },
    { phrase: "hype", weight: 1, aliases: ["hyped"] },
    { phrase: "overrated", weight: 1 },
    { phrase: "underrated", weight: 1 }
  ]
};
