import { normalizeTeamName } from "./teamName";
import { buildTeamPhrases, type TeamPhraseDefinition } from "./trendsPhrases";
import {
  narrativeEdgeFromScores,
  summarizeTeamNarrative,
  summarizeVolatility,
  type NarrativeTemperatureResponse,
  type PhraseSignalInput,
} from "./trendsScoring";

type TrendsTimelinePoint = { value: number[] };
type TrendsMultilineResponse = { default?: { timelineData?: TrendsTimelinePoint[] } };
type TrendsExploreWidget = { id?: string; token?: string; request?: unknown };
type TrendsExploreResponse = { widgets?: TrendsExploreWidget[] };

type PhraseFetchOutcome = {
  signal: PhraseSignalInput;
  ok: boolean;
};

const GOOGLE_TRENDS_EXPLORE_URL = "https://trends.google.com/trends/api/explore";
const GOOGLE_TRENDS_TIMESERIES_URL = "https://trends.google.com/trends/api/widgetdata/multiline";
const GOOGLE_PREFIX = ")]}'";
const REGION = "US";
const HL = "en-US";
const TIME_WINDOW = "now 7-d";

const TEAM_QUERY_ALIASES: Record<string, string> = {
  "miami oh": "Miami Ohio basketball",
  "saint johns": "St John's basketball",
  "st johns": "St John's basketball",
  "uconn": "Connecticut Huskies basketball",
  "unc": "North Carolina Tar Heels basketball",
  "smu": "SMU basketball",
  "vcu": "VCU basketball",
  "byu": "BYU basketball",
  "ole miss": "Ole Miss basketball",
  "lsu": "LSU basketball",
  "usc": "USC basketball",
  "ucla": "UCLA basketball",
  "uc irvine": "UC Irvine basketball",
  "uc san diego": "UC San Diego basketball",
  "utah st": "Utah State basketball",
  "boise st": "Boise State basketball",
  "san jose st": "San Jose State basketball",
};

const trimGooglePrefix = (text: string): string => text.startsWith(GOOGLE_PREFIX) ? text.slice(GOOGLE_PREFIX.length) : text;

export const sanitizeTeamQueryName = (teamName: string): string => {
  const normalized = normalizeTeamName(teamName);
  return TEAM_QUERY_ALIASES[normalized] ?? `${teamName.replace(/\bSt\.?\b/g, "State").replace(/\s+/g, " ").trim()} basketball`;
};

const buildPhraseQueries = (teamName: string): TeamPhraseDefinition[] => {
  const safeTeamName = sanitizeTeamQueryName(teamName);
  return buildTeamPhrases(safeTeamName);
};

const getExploreWidget = async (phrase: string): Promise<TrendsExploreWidget | null> => {
  const req = {
    comparisonItem: [{ keyword: phrase, geo: REGION, time: TIME_WINDOW }],
    category: 0,
    property: "",
  };
  const url = new URL(GOOGLE_TRENDS_EXPLORE_URL);
  url.searchParams.set("hl", HL);
  url.searchParams.set("tz", "0");
  url.searchParams.set("req", JSON.stringify(req));

  const response = await fetch(url.toString(), {
    headers: { "user-agent": "Mozilla/5.0 NarrativeTemperature/1.0" },
  });
  if (!response.ok) throw new Error(`Explore request failed (${response.status})`);
  const payload = JSON.parse(trimGooglePrefix(await response.text())) as TrendsExploreResponse;
  return payload.widgets?.find((widget) => widget.id === "TIMESERIES") ?? null;
};

const fetchTimeline = async (phrase: string): Promise<number[]> => {
  const widget = await getExploreWidget(phrase);
  if (!widget?.token || !widget.request) return [];

  const url = new URL(GOOGLE_TRENDS_TIMESERIES_URL);
  url.searchParams.set("hl", HL);
  url.searchParams.set("tz", "0");
  url.searchParams.set("req", JSON.stringify(widget.request));
  url.searchParams.set("token", widget.token);

  const response = await fetch(url.toString(), {
    headers: { "user-agent": "Mozilla/5.0 NarrativeTemperature/1.0" },
  });
  if (!response.ok) throw new Error(`Timeline request failed (${response.status})`);
  const payload = JSON.parse(trimGooglePrefix(await response.text())) as TrendsMultilineResponse;
  return (payload.default?.timelineData ?? [])
    .map((point) => Array.isArray(point.value) ? point.value[0] : 0)
    .filter((value) => typeof value === "number" && Number.isFinite(value));
};

const toSignal = (phraseDef: TeamPhraseDefinition, timeline: number[]): PhraseSignalInput => {
  const values = timeline.length ? timeline : [0];
  const peak = Math.max(...values);
  const baselineWindow = values.slice(0, Math.max(1, values.length - 2));
  const baseline = baselineWindow.reduce((sum, value) => sum + value, 0) / Math.max(1, baselineWindow.length);
  const trendScore = peak;
  const normalizedScore = peak <= 0 ? 0 : Math.min(1, Math.max(0, ((peak - baseline) / Math.max(peak, 1)) * 1.35));
  return {
    ...phraseDef,
    trendScore,
    normalizedScore,
    sampleCount: values.length,
  };
};

const fetchPhraseSignal = async (phraseDef: TeamPhraseDefinition): Promise<PhraseFetchOutcome> => {
  try {
    const timeline = await fetchTimeline(phraseDef.phrase);
    return { signal: toSignal(phraseDef, timeline), ok: true };
  } catch {
    return {
      signal: { ...phraseDef, trendScore: 0, normalizedScore: 0, sampleCount: 0 },
      ok: false,
    };
  }
};

const fetchTeamSignals = async (teamName: string): Promise<{ signals: PhraseSignalInput[]; okCount: number }> => {
  const phraseDefs = buildPhraseQueries(teamName);
  const outcomes = await Promise.all(phraseDefs.map((phraseDef) => fetchPhraseSignal(phraseDef)));
  return {
    signals: outcomes.map((outcome) => outcome.signal),
    okCount: outcomes.filter((outcome) => outcome.ok).length,
  };
};

export const getNarrativeTemperature = async (teamA: string, teamB: string): Promise<NarrativeTemperatureResponse> => {
  const [aData, bData] = await Promise.all([fetchTeamSignals(teamA), fetchTeamSignals(teamB)]);
  const teamASummary = summarizeTeamNarrative(teamA, aData.signals);
  const teamBSummary = summarizeTeamNarrative(teamB, bData.signals);
  const volatility = summarizeVolatility(teamASummary, teamBSummary);
  const narrativeEdge = narrativeEdgeFromScores(teamA, teamASummary.score, teamB, teamBSummary.score);
  const totalOk = aData.okCount + bData.okCount;
  const totalSignals = aData.signals.length + bData.signals.length;
  const sourceStatus = totalOk === 0 ? "unavailable" : totalOk < totalSignals ? "partial" : "live";
  const sourceNote = sourceStatus === "live"
    ? "Soft signal based on Google Trends phrase activity."
    : sourceStatus === "partial"
      ? "Soft signal based on available Google Trends phrase activity; some phrases had limited data."
      : "Trends data unavailable right now.";

  return {
    teamA: teamASummary,
    teamB: teamBSummary,
    volatility,
    narrativeEdge,
    fetchedAt: new Date().toISOString(),
    sourceStatus,
    sourceNote,
  };
};
