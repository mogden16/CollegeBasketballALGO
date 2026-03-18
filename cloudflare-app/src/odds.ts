import { normalizeTeamName } from "./teamName";

export const PREFERRED_BOOKMAKERS = [
  "fanduel",
  "bovada",
  "draftkings",
  "betmgm",
  "caesars",
  "espnbet",
  "bet365",
] as const;

export type PreferredBookmaker = (typeof PREFERRED_BOOKMAKERS)[number];
export type VegasInfo = {
  spread: number | null;
  total: number | null;
  source: PreferredBookmaker | null;
  status: "ok" | "partial" | "unavailable";
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
  "saint johns": "saint johns",
  "saint john": "saint johns",
  "st josephs": "saint josephs",
  usc: "southern california",
  "usc trojans": "southern california",
  uconn: "connecticut",
  "u conn": "connecticut",
  "miami fl": "miami",
  "ole miss": "mississippi",
  "nc a t": "north carolina a t",
};

const SAINT_SCHOOL_RE = /^state (bonaventure|francis|johns|josephs|louis|marys|peters|thomas)\b/;

export const BOOKMAKER_DISPLAY_NAMES: Record<PreferredBookmaker, string> = {
  fanduel: "FanDuel",
  bovada: "Bovada",
  draftkings: "DraftKings",
  betmgm: "BetMGM",
  caesars: "Caesars",
  espnbet: "ESPN BET",
  bet365: "bet365",
};

export const normalizeOddsTeamName = (name: string): string => {
  let normalized = normalizeTeamName(name)
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/\buniversity\b/g, "")
    .replace(/\bthe\b/g, "")
    .replace(/\bst\b/g, "state")
    .replace(/\s+/g, " ")
    .trim();

  if (SAINT_SCHOOL_RE.test(normalized)) {
    normalized = `saint${normalized.slice("state".length)}`.trim();
  }

  return TEAM_ALIASES[normalized] ?? normalized;
};

const teamKeysMatch = (keyA: string, keyB: string): boolean => {
  if (keyA === keyB) return true;
  const [shorter, longer] = keyA.length <= keyB.length ? [keyA, keyB] : [keyB, keyA];
  return longer.startsWith(shorter) && longer[shorter.length] === " ";
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

export const lineUnavailable = (): VegasInfo => ({ spread: null, total: null, source: null, status: "unavailable" });

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

const normalizeBookmakerKey = (bookmaker: OddsBookmaker): string => normalizeTeamName(String(bookmaker.key ?? bookmaker.title ?? ""));

const extractSpread = (event: OddsEvent, spreadMarket: OddsMarket | undefined): number | null => {
  if (!spreadMarket) return null;
  const homeTeamKey = normalizeOddsTeamName(String(event.home_team ?? ""));
  const homeOutcome = (spreadMarket.outcomes ?? []).find((outcome) => normalizeOddsTeamName(String(outcome.name ?? "")) === homeTeamKey);
  const point = toFiniteNumber(homeOutcome?.point);
  if (point === null) return null;

  // App convention: vegas.spread is always the line for the home team.
  // Negative means the home team is favored; positive means the away team is favored.
  return point;
};

const extractLineFromBookmaker = (event: OddsEvent, bookmaker: OddsBookmaker, bookmakerKey: PreferredBookmaker): VegasInfo => {
  const markets = bookmaker.markets ?? [];
  const spreads = markets.find((market) => market.key === "spreads");
  const totals = markets.find((market) => market.key === "totals");
  const spread = extractSpread(event, spreads);
  const total = extractTotal(totals);

  if (spread === null && total === null) {
    console.log(`[Vegas] Book ${bookmakerKey} had no usable markets for ${event.away_team} @ ${event.home_team}`);
    return lineUnavailable();
  }

  const status = spread !== null && total !== null ? "ok" : "partial";
  console.log(
    `[Vegas] Selected ${bookmakerKey} for ${event.away_team} @ ${event.home_team} (spread=${spread ?? "N/A"}, total=${total ?? "N/A"}, status=${status})`,
  );

  return { spread, total, source: bookmakerKey, status };
};

export const extractPreferredVegasLine = (event: OddsEvent): VegasInfo => {
  const bookmakers = event.bookmakers ?? [];
  for (const preferredBookmaker of PREFERRED_BOOKMAKERS) {
    const bookmaker = bookmakers.find((candidate) => normalizeBookmakerKey(candidate) === preferredBookmaker);
    if (!bookmaker) {
      console.log(`[Vegas] Book ${preferredBookmaker} missing for ${event.away_team} @ ${event.home_team}`);
      continue;
    }
    const line = extractLineFromBookmaker(event, bookmaker, preferredBookmaker);
    if (line.status !== "unavailable") return line;
  }
  return lineUnavailable();
};

const gameLookupKey = (homeTeam: string, awayTeam: string): string => `${normalizeOddsTeamName(homeTeam)}|${normalizeOddsTeamName(awayTeam)}`;

export const getGameLookupKey = (homeTeam: string, awayTeam: string): string => gameLookupKey(homeTeam, awayTeam);

export const matchOddsEventToGame = (selectedDate: string, game: MatchableGame, event: OddsEvent): boolean => {
  const eventDate = event.commence_time ? toEasternIsoDate(event.commence_time) : null;
  if (eventDate !== selectedDate) return false;

  const gameHomeKey = normalizeOddsTeamName(game.homeTeam);
  const gameAwayKey = normalizeOddsTeamName(game.awayTeam);
  const eventHomeKey = normalizeOddsTeamName(String(event.home_team ?? ""));
  const eventAwayKey = normalizeOddsTeamName(String(event.away_team ?? ""));
  const homeMatch = teamKeysMatch(gameHomeKey, eventHomeKey);
  const awayMatch = teamKeysMatch(gameAwayKey, eventAwayKey);

  console.log(
    `[Vegas] Compare game '${gameAwayKey} @ ${gameHomeKey}' vs odds '${eventAwayKey} @ ${eventHomeKey}' => home=${homeMatch} away=${awayMatch}`,
  );

  return homeMatch && awayMatch;
};

export const fetchOddsForDate = async (
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
  url.searchParams.set("bookmakers", PREFERRED_BOOKMAKERS.join(","));
  url.searchParams.set("oddsFormat", "american");
  url.searchParams.set("dateFormat", "iso");
  url.searchParams.set("commenceTimeFrom", start);
  url.searchParams.set("commenceTimeTo", end);

  const response = await fetchFn(url.toString(), { cf: { cacheTtl: 120, cacheEverything: false } });
  if (!response.ok) return [];
  const data = (await response.json()) as unknown;
  return Array.isArray(data) ? (data as OddsEvent[]) : [];
};

export const fetchOddsApiEventsForDate = fetchOddsForDate;

export const buildOddsLookupForDate = (selectedDate: string, games: MatchableGame[], events: OddsEvent[]): Map<string, VegasInfo> => {
  const dateEvents = events.filter((event) => {
    const eventDate = event.commence_time ? toEasternIsoDate(event.commence_time) : null;
    return eventDate === selectedDate;
  });

  console.log(`[Vegas] ${dateEvents.length} odds events for ${selectedDate}, matching against ${games.length} games`);

  const eventByKey = new Map<string, OddsEvent>();
  for (const event of dateEvents) {
    const key = gameLookupKey(String(event.home_team ?? ""), String(event.away_team ?? ""));
    if (!eventByKey.has(key)) eventByKey.set(key, event);
  }

  const lookup = new Map<string, VegasInfo>();
  for (const game of games) {
    const gameKey = gameLookupKey(game.homeTeam, game.awayTeam);
    let matchedEvent = eventByKey.get(gameKey);

    if (!matchedEvent) {
      matchedEvent = dateEvents.find((candidate) => matchOddsEventToGame(selectedDate, game, candidate));
      if (matchedEvent) {
        const oddsKey = gameLookupKey(String(matchedEvent.home_team ?? ""), String(matchedEvent.away_team ?? ""));
        console.log(`[Vegas] Matched '${gameKey}' -> '${oddsKey}'`);
      } else {
        console.log(`[Vegas] Unmatched game '${gameKey}' for ${selectedDate}`);
      }
    }

    lookup.set(gameKey, matchedEvent ? extractPreferredVegasLine(matchedEvent) : lineUnavailable());
  }

  return lookup;
};
