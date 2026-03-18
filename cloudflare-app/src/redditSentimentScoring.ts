import { REDDIT_LOW_SIGNAL_SAMPLE_THRESHOLD, REDDIT_SENTIMENT_PHRASES, type SentimentCategory } from "./redditSentimentPhrases";
import type { TeamQueryProfile } from "./teamQueryNormalization";

export type RedditTextSample = {
  id: string;
  body: string;
  createdUtc: number;
  score: number;
  url?: string;
};

export type PhraseHit = {
  phrase: string;
  category: SentimentCategory;
  count: number;
  weightedImpact: number;
};

export type TeamSentimentSummary = {
  teamName: string;
  score: number;
  label: string;
  topThemes: string[];
  phraseHits: PhraseHit[];
  sampleCount: number;
};

export type NarrativeEdge = { label: string };
export type VolatilitySummary = { score: number; label: string };

const ONE_DAY_SECONDS = 86400;

const countOccurrences = (haystack: string, needle: string): number => {
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`(^|[^a-z])${escaped}([^a-z]|$)`, "gi");
  const matches = haystack.match(regex);
  return matches ? matches.length : 0;
};

const sampleWeight = (sample: RedditTextSample): number => {
  const ageDays = Math.max(0, (Date.now() / 1000 - sample.createdUtc) / ONE_DAY_SECONDS);
  const recencyBoost = Math.max(0.6, 1.35 - ageDays * 0.08);
  const scoreBoost = Math.min(1.4, 1 + Math.max(0, sample.score) / 150);
  return recencyBoost * scoreBoost;
};

const scoreLabel = (score: number): string => {
  if (score <= -2.5) return "Risk";
  if (score <= -1) return "Cool";
  if (score < 1) return "Neutral";
  if (score < 2.5) return "Warm";
  return "Hot";
};

export const computeVolatility = (teamAHits: PhraseHit[], teamBHits: PhraseHit[]): VolatilitySummary => {
  const buzzCount = [...teamAHits, ...teamBHits]
    .filter((hit) => hit.category === "buzz")
    .reduce((sum, hit) => sum + hit.count, 0);

  const score = +Math.min(10, buzzCount * 1.15).toFixed(1);
  const label = score >= 6 ? "High" : score >= 2.5 ? "Moderate" : "Low";
  return { score, label };
};

export const computeNarrativeEdge = (teamA: TeamSentimentSummary, teamB: TeamSentimentSummary): NarrativeEdge => {
  const diff = +(teamA.score - teamB.score).toFixed(2);
  if (Math.abs(diff) < 1) return { label: "Neutral" };
  const leader = diff > 0 ? teamA.teamName : teamB.teamName;
  if (Math.abs(diff) >= 2.5) return { label: `Clearer edge: ${leader}` };
  return { label: `Slight edge: ${leader}` };
};

export const scoreTeamSentiment = (team: TeamQueryProfile, samples: RedditTextSample[]): TeamSentimentSummary => {
  const hits = new Map<string, PhraseHit>();
  let totalScore = 0;

  for (const sample of samples) {
    const text = sample.body.toLowerCase();
    const weightFactor = sampleWeight(sample);

    for (const category of Object.keys(REDDIT_SENTIMENT_PHRASES) as SentimentCategory[]) {
      for (const definition of REDDIT_SENTIMENT_PHRASES[category]) {
        const searchTerms = [definition.phrase, ...(definition.aliases ?? [])];
        const count = searchTerms.reduce((sum, term) => sum + countOccurrences(text, term.toLowerCase()), 0);
        if (!count) continue;
        const key = `${category}:${definition.phrase}`;
        const prev = hits.get(key) ?? { phrase: definition.phrase, category, count: 0, weightedImpact: 0 };
        prev.count += count;
        prev.weightedImpact = +(prev.weightedImpact + count * definition.weight * weightFactor).toFixed(2);
        hits.set(key, prev);
        if (category !== "buzz") {
          totalScore += count * definition.weight * weightFactor;
        }
      }
    }
  }

  const phraseHits = [...hits.values()].sort((a, b) => Math.abs(b.weightedImpact) - Math.abs(a.weightedImpact) || b.count - a.count);
  const normalizedScore = samples.length ? +(totalScore / Math.max(1, samples.length * 0.85)).toFixed(2) : 0;
  const topThemes = phraseHits.slice(0, 4).map((hit) => hit.category === "buzz" ? `${hit.phrase} buzz` : hit.phrase);
  const sampleCount = samples.length;
  const lowSignal = sampleCount < REDDIT_LOW_SIGNAL_SAMPLE_THRESHOLD;

  return {
    teamName: team.displayName,
    score: normalizedScore,
    label: lowSignal ? `Limited (${scoreLabel(normalizedScore)})` : scoreLabel(normalizedScore),
    topThemes,
    phraseHits,
    sampleCount,
  };
};
