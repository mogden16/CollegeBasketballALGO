import { normalizeTeamName } from "./teamName";

export type LineProjection = { spread: number | null; total: number | null };
export type VegasInfo = LineProjection & {
  vegasSource?: "fanduel";
  vegasStatus?: "available" | "unavailable";
};

type OddsOutcome = { name?: string; point?: number };
type OddsMarket = { key?: string; outcomes?: OddsOutcome[] };
type OddsBookmaker = { key?: string; title?: string; markets?: OddsMarket[] };
export type OddsEvent = {
  id?: string;
  commence_time?: string;
  home_team?: string;
  away_team?: string;
  bookmakers?: OddsBookmaker[];
};

type MatchableGame = { homeTeam: string; awayTeam: string; gameTimeEt?: string | null };

const SPORT_KEY = "basketball_ncaab";

const TEAM_ALIASES: Record<string, string> = {
  "nc state": "north carolina state",
  "n c state": "north carolina state",
  "st johns": "saint johns",
  "st john": "saint johns",
  "st marys": "saint marys",
  "saint josephs": "st josephs",
  usc: "southern california",
  "usc trojans": "southern california",
  uconn: "connecticut",
  "u conn": "connecticut",
  "miami fl": "miami",
  "ole miss": "mississippi",
  smu: "southern methodist",
  uab: "alabama birmingham",
};

const toCanonicalTeamKey = (name: string): string => {
  const normalized = normalizeTeamName(name)
    .replace(/\buniversity\b/g, "")
    .replace(/\bthe\b/g, "")
    .replace(/\bstate\b/g, "state")
    .replace(/\bsaint\b/g, "saint")
    .replace(/\bst\b/g, "saint")
    .replace(/\s+/g, " ")
    .trim();
  return TEAM_ALIASES[normalized] ?? normalized;
};

const toEasternIsoDate = (isoDateTime: string): string | null => {
  const dt = new Date(isoDateTime);
  if (Number.isNaN(dt.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(dt);
  const year = parts.find((p) => p.type === "year")?.value;
  const month = parts.find((p) => p.type === "month")?.value;
  const day = parts.find((p) => p.type === "day")?.value;
  if (!year || !month || !day) return null;
  return `${year}-${month}-${day}`;
};

const lineUnavailable = (): VegasInfo => ({ spread: null, total: null, vegasSource: "fanduel", vegasStatus: "unavailable" });

const toFiniteNumber = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

const extractTotal = (market: OddsMarket | undefined): number | null => {
  if (!market) return null;
  const outcomes = market.outcomes ?? [];
  const over = outcomes.find((outcome) => normalizeTeamName(String(outcome.name ?? "")) === "over");
  if (over) return toFiniteNumber(over.point);
  for (const outcome of outcomes) {
    const point = toFiniteNumber(outcome.point);
    if (point !== null) return point;
  }
  return null;
};

/**
 * Convention: `spread` is always the home-team spread.
 * Negative means home favorite. Positive means home underdog.
 */
export const extractFanDuelLineFromEvent = (event: OddsEvent): VegasInfo => {
  const bookmakers = event.bookmakers ?? [];
  const fanduel = bookmakers.find((bookmaker) => normalizeTeamName(String(bookmaker.key ?? bookmaker.title ?? "")) === "fanduel");
  if (!fanduel) return lineUnavailable();

  const markets = fanduel.markets ?? [];
  const spreads = markets.find((market) => market.key === "spreads");
  const totals = markets.find((market) => market.key === "totals");

  const homeTeam = toCanonicalTeamKey(String(event.home_team ?? ""));
  const spreadOutcome = (spreads?.outcomes ?? []).find((outcome) => toCanonicalTeamKey(String(outcome.name ?? "")) === homeTeam);
  const spread = toFiniteNumber(spreadOutcome?.point);
  const total = extractTotal(totals);

  if (spread === null || total === null) {
    return { spread, total, vegasSource: "fanduel", vegasStatus: "unavailable" };
  }
  return { spread, total, vegasSource: "fanduel", vegasStatus: "available" };
};

const gameLookupKey = (homeTeam: string, awayTeam: string): string => `${toCanonicalTeamKey(homeTeam)}|${toCanonicalTeamKey(awayTeam)}`;

export const matchOddsEventToGame = (selectedDate: string, game: MatchableGame, event: OddsEvent): boolean => {
  const eventDate = event.commence_time ? toEasternIsoDate(event.commence_time) : null;
  if (eventDate !== selectedDate) return false;
  return gameLookupKey(game.homeTeam, game.awayTeam) === gameLookupKey(String(event.home_team ?? ""), String(event.away_team ?? ""));
};

export const fetchOddsApiEventsForDate = async (
  selectedDate: string,
  oddsApiKey: string | undefined,
  fetchFn: typeof fetch = fetch,
): Promise<OddsEvent[]> => {
  if (!oddsApiKey) return [];
  const start = `${selectedDate}T00:00:00Z`;
  const dayEnd = new Date(`${selectedDate}T00:00:00Z`);
  dayEnd.setUTCDate(dayEnd.getUTCDate() + 2);
  const end = `${dayEnd.toISOString().slice(0, 10)}T06:00:00Z`;

  const url = new URL(`https://api.the-odds-api.com/v4/sports/${SPORT_KEY}/odds`);
  url.searchParams.set("apiKey", oddsApiKey);
  url.searchParams.set("regions", "us");
  url.searchParams.set("markets", "spreads,totals");
  url.searchParams.set("bookmakers", "fanduel");
  url.searchParams.set("oddsFormat", "american");
  url.searchParams.set("dateFormat", "iso");
  url.searchParams.set("commenceTimeFrom", start);
  url.searchParams.set("commenceTimeTo", end);

  const response = await fetchFn(url.toString(), { cf: { cacheTtl: 120, cacheEverything: false } });
  if (!response.ok) return [];
  const data = (await response.json()) as unknown;
  return Array.isArray(data) ? (data as OddsEvent[]) : [];
};

export const buildFanDuelLookupForDate = (selectedDate: string, games: MatchableGame[], events: OddsEvent[]): Map<string, VegasInfo> => {
  const eventByKey = new Map<string, OddsEvent>();
  for (const event of events) {
    const eventDate = event.commence_time ? toEasternIsoDate(event.commence_time) : null;
    if (eventDate !== selectedDate) continue;
    const key = gameLookupKey(String(event.home_team ?? ""), String(event.away_team ?? ""));
    if (!eventByKey.has(key)) eventByKey.set(key, event);
  }

  const lookup = new Map<string, VegasInfo>();
  for (const game of games) {
    const key = gameLookupKey(game.homeTeam, game.awayTeam);
    const event = eventByKey.get(key) ?? events.find((candidate) => matchOddsEventToGame(selectedDate, game, candidate));
    if (!event) {
      lookup.set(key, lineUnavailable());
      continue;
    }
    lookup.set(key, extractFanDuelLineFromEvent(event));
  }
  return lookup;
};

export const getGameLookupKey = (homeTeam: string, awayTeam: string): string => gameLookupKey(homeTeam, awayTeam);
