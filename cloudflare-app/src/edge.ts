export type EdgeModel = {
  spreadEdge: number | null;
  totalEdge: number | null;
};

export type DistanceInfo = {
  homeMiles: number | null;
  awayMiles: number | null;
};

export const sideSignal = (edge: number | null, threshold: number): "home" | "away" | null => {
  if (edge === null || Math.abs(edge) < threshold) {
    return null;
  }
  return edge > 0 ? "home" : "away";
};

export const totalSignal = (edge: number | null, threshold: number): "over" | "under" | null => {
  if (edge === null || Math.abs(edge) < threshold) {
    return null;
  }
  return edge > 0 ? "over" : "under";
};

export const computeSideEdge = (
  kenpom: EdgeModel,
  trank: EdgeModel,
  threshold: number,
  distance?: DistanceInfo | null,
) => {
  const points = { home: 0, away: 0 };
  const reasons: string[] = [];

  const kp = sideSignal(kenpom.spreadEdge, threshold);
  if (kp) {
    points[kp] += 1;
    reasons.push(`KenPom→${kp}`);
  }

  const tr = sideSignal(trank.spreadEdge, threshold);
  if (tr) {
    points[tr] += 1;
    reasons.push(`T-Rank→${tr}`);
  }

  if (
    distance &&
    distance.homeMiles !== null &&
    distance.awayMiles !== null &&
    distance.homeMiles !== distance.awayMiles
  ) {
    const closer = distance.homeMiles < distance.awayMiles ? "home" : "away";
    points[closer] += 1;
    reasons.push(`Distance→${closer}`);
  }

  const side = points.home === points.away ? null : points.home > points.away ? "home" : "away";
  const score = Math.max(points.home, points.away);
  return { score, side, reasons, highlight: score > 1, breakdown: points };
};

export const computeTotalEdge = (kenpom: EdgeModel, trank: EdgeModel, threshold: number) => {
  const points = { over: 0, under: 0 };
  const reasons: string[] = [];

  const kp = totalSignal(kenpom.totalEdge, threshold);
  if (kp) {
    points[kp] += 1;
    reasons.push(`KenPom→${kp}`);
  }

  const tr = totalSignal(trank.totalEdge, threshold);
  if (tr) {
    points[tr] += 1;
    reasons.push(`T-Rank→${tr}`);
  }

  const side = points.over === points.under ? null : points.over > points.under ? "over" : "under";
  const score = Math.max(points.over, points.under);
  return { score, side, reasons, highlight: score > 1, breakdown: points };
};
