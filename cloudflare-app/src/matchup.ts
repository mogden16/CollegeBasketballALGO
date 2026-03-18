export type TeamRatings = {
  adjO: number;
  adjD: number;
  adjT: number;
  sourceName?: string;
  record?: string | null;
  netRtg?: number | null;
  ortg?: number | null;
  drtg?: number | null;
  luck?: number | null;
};

export type KenPomTeamInfo = {
  team: string;
  record: string | null;
  netRating: number | null;
  offRating: number | null;
  defRating: number | null;
  adjTempo: number | null;
  luck: number | null;
};

/** Projection from a single source model (KenPom or T-Rank). */
export type SourceProjection = {
  /** Team A (visiting/away) projected score. */
  teamAScore: number;
  /** Team B (home) projected score. */
  teamBScore: number;
  /**
   * Projected margin from Team A's perspective.
   * Positive = Team A projected to win.
   * Negative = Team B projected to win.
   */
  spread: number;
  /** Projected combined score. */
  total: number;
};

export type MatchupResult = {
  teamA: string;
  teamB: string;
  neutral: boolean;
  useDampening: boolean;
  kenpom: SourceProjection | null;
  trank: SourceProjection | null;
  consensus: SourceProjection | null;
  kenpomTeamInfo: {
    teamA: KenPomTeamInfo | null;
    teamB: KenPomTeamInfo | null;
  };
  notes: string[];
};

// ── Model constants (tuned coefficients) ────────────────────────────────────
export const LAMBDA = 0.8905;         // regression-to-mean shrinkage factor
export const AVG_EFFICIENCY = 100;    // league average adjusted efficiency
export const HCA = 1.9895;            // home court advantage (points)
export const TEMPO_SCALE = 0.9290;    // tempo regression multiplier

/**
 * Compute a score projection for a single matchup.
 *
 * teamA = visiting / away team
 * teamB = home team (or second-listed on neutral site)
 *
 * With dampening ON:  uses λ-shrinkage, TEMPO_SCALE, and built-in HCA.
 * With dampening OFF: λ=1, TEMPO_SCALE=1, no built-in HCA
 *                     (manual slider controls home court instead).
 */
export function predictGame(
  teamA: TeamRatings,
  teamB: TeamRatings,
  neutral: boolean,
  useDampening: boolean
): SourceProjection {
  const lambda      = useDampening ? LAMBDA       : 1.0;
  const tempoScale  = useDampening ? TEMPO_SCALE  : 1.0;
  // Built-in HCA only active when dampening ON and not a neutral site.
  const builtinHCA  = useDampening && !neutral ? HCA : 0;

  const tempo  = tempoScale * (teamA.adjT + teamB.adjT) / 2;
  const effA   = teamA.adjO + lambda * (teamB.adjD - AVG_EFFICIENCY);
  const effB   = teamB.adjO + lambda * (teamA.adjD - AVG_EFFICIENCY);
  const rawA   = (tempo * effA) / 100;
  const rawB   = (tempo * effB) / 100;

  // Spread from Team A's perspective (positive = A wins)
  const spread = rawA - rawB - builtinHCA;

  return {
    teamAScore : +rawA.toFixed(1),
    teamBScore : +rawB.toFixed(1),
    spread     : +spread.toFixed(1),
    total      : +(rawA + rawB).toFixed(1),
  };
}

/** Average two projections into a consensus. */
export function buildConsensus(a: SourceProjection, b: SourceProjection): SourceProjection {
  return {
    teamAScore : +((a.teamAScore + b.teamAScore) / 2).toFixed(1),
    teamBScore : +((a.teamBScore + b.teamBScore) / 2).toFixed(1),
    spread     : +((a.spread     + b.spread    ) / 2).toFixed(1),
    total      : +((a.total      + b.total     ) / 2).toFixed(1),
  };
}
