import type { NarrativePhraseCategory, TeamPhraseDefinition } from "./trendsPhrases";

export type PhraseTrendResult = {
  phrase: string;
  term: string;
  category: NarrativePhraseCategory;
  trendScore: number;
  normalizedScore: number;
  weightedImpact: number;
  isSpiking: boolean;
  sampleCount: number;
};

export type TeamNarrativeSummary = {
  team: string;
  score: number;
  label: "Hot" | "Warm" | "Neutral" | "Cool" | "Risk";
  badge: "Hot" | "Warm" | "Neutral" | "Cool" | "Risk";
  phrases: PhraseTrendResult[];
  topPhrases: PhraseTrendResult[];
};

export type VolatilitySummary = {
  score: number;
  label: "Low" | "Moderate" | "High";
};

export type NarrativeEdgeSummary = {
  label: string;
  strength: "neutral" | "slight" | "clear";
};

export type NarrativeTemperatureResponse = {
  teamA: TeamNarrativeSummary;
  teamB: TeamNarrativeSummary;
  volatility: VolatilitySummary;
  narrativeEdge: NarrativeEdgeSummary;
  fetchedAt: string;
  sourceStatus: "live" | "partial" | "unavailable";
  sourceNote: string;
};

export type PhraseSignalInput = TeamPhraseDefinition & {
  trendScore: number;
  normalizedScore: number;
  sampleCount: number;
};

const round1 = (value: number): number => Math.round(value * 10) / 10;
const round2 = (value: number): number => Math.round(value * 100) / 100;

export const isPhraseSpiking = (normalizedScore: number, trendScore: number): boolean =>
  normalizedScore >= 0.55 || trendScore >= 70;

export const scorePhraseSignal = (signal: PhraseSignalInput): PhraseTrendResult => {
  const spikeBoost = isPhraseSpiking(signal.normalizedScore, signal.trendScore) ? 1.15 : 1;
  return {
    phrase: signal.phrase,
    term: signal.term,
    category: signal.category,
    trendScore: round1(signal.trendScore),
    normalizedScore: round2(signal.normalizedScore),
    weightedImpact: round2(signal.weight * signal.normalizedScore * spikeBoost),
    isSpiking: spikeBoost > 1,
    sampleCount: signal.sampleCount,
  };
};

export const narrativeLabelFromScore = (score: number): TeamNarrativeSummary["label"] => {
  if (score <= -2.5) return "Risk";
  if (score <= -1.0) return "Cool";
  if (score < 1.0) return "Neutral";
  if (score < 2.5) return "Warm";
  return "Hot";
};

export const volatilityLabelFromScore = (score: number): VolatilitySummary["label"] => {
  if (score >= 2.6) return "High";
  if (score >= 1.2) return "Moderate";
  return "Low";
};

export const narrativeEdgeFromScores = (teamA: string, scoreA: number, teamB: string, scoreB: number): NarrativeEdgeSummary => {
  const diff = scoreA - scoreB;
  const abs = Math.abs(diff);
  if (abs < 1.0) return { label: "Narrative Edge: Neutral", strength: "neutral" };
  const leader = diff > 0 ? teamA : teamB;
  if (abs >= 2.5) return { label: `Narrative Edge: Clearer ${leader}` , strength: "clear" };
  return { label: `Narrative Edge: Slight ${leader}`, strength: "slight" };
};

export const summarizeTeamNarrative = (team: string, signals: PhraseSignalInput[]): TeamNarrativeSummary => {
  const scored = signals.map(scorePhraseSignal);
  const score = round2(scored.reduce((sum, item) => sum + item.weightedImpact, 0));
  const sorted = [...scored].sort((a, b) => {
    const spikeDelta = Number(b.isSpiking) - Number(a.isSpiking);
    if (spikeDelta !== 0) return spikeDelta;
    const impactDelta = Math.abs(b.weightedImpact) - Math.abs(a.weightedImpact);
    if (impactDelta !== 0) return impactDelta;
    return b.trendScore - a.trendScore;
  });
  const label = narrativeLabelFromScore(score);
  return {
    team,
    score,
    label,
    badge: label,
    phrases: sorted,
    topPhrases: sorted.filter((item) => item.normalizedScore > 0.08).slice(0, 5),
  };
};

export const summarizeVolatility = (teamA: TeamNarrativeSummary, teamB: TeamNarrativeSummary): VolatilitySummary => {
  const buzzPhrases = [...teamA.phrases, ...teamB.phrases].filter((item) => item.category === "buzz");
  const score = round2(
    buzzPhrases.reduce((sum, item) => sum + item.normalizedScore + (item.isSpiking ? 0.35 : 0), 0) / Math.max(1, buzzPhrases.length / 2),
  );
  return { score, label: volatilityLabelFromScore(score) };
};
