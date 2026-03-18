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

// Post-normalization aliases: keys are looked up AFTER normalizeTeamName + regex expansions.
// The "st" → "state" regex runs first, so keys must use the post-expansion form.
// E.g. "nc state" (not "n.c. state") and "state john" (not "st john").
const TEAM_ALIASES: Record<string, string> = {
  "nc state": "north carolina state",
  "n c state": "north carolina state",
  // "st john" → (st→state regex) → "state john"; alias to canonical plural
  "state john": "saint johns",
  usc: "southern california",
  "usc trojans": "southern california",
  uconn: "connecticut",
  "u conn": "connecticut",
  "miami fl": "miami",
  "ole miss": "mississippi",
};

// Schools whose names start with "Saint" / "St." (not "X State").
// The general "st" → "state" expansion would wrongly turn "St. John's" into "state johns".
// After expansion, these prefixes are converted back to "saint".
const SAINT_SCHOOL_RE = /^state (bonaventure|francis|johns|josephs|louis|marys|peters|thomas)\b/;

const toCanonicalTeamKey = (name: string): string => {
  let normalized = normalizeTeamName(name)
    .replace(/\s*\([^)]*\)/g, "") // remove parenthetical disambiguation e.g. "(OH)", "(NY)", "(CA)"
    .replace(/\buniversity\b/g, "")
    .replace(/\bthe\b/g, "")
    // Expand ALL standalone "st" → "state".
    // This correctly handles "Iowa St." → "iowa state", "Cal St. Bakersfield" → "cal state bakersfield",
    // "Arizona St." → "arizona state", etc.  Does NOT affect "state", "eastern", "boston", etc.
    .replace(/\bst\b/g, "state")
    .replace(/\s+/g, " ")
    .trim();

  // Re-apply "saint" for schools whose name genuinely starts with "Saint" / "St."
  // (e.g. "St. John's" → after expansion "state johns" → re-map → "saint johns")
  if (SAINT_SCHOOL_RE.test(normalized)) {
    normalized = "saint" + normalized.slice("state".length);
  }

  return TEAM_ALIASES[normalized] ?? normalized;
};

/**
 * Returns true if two canonical team keys refer to the same team.
 *
 * Handles the ESPN-short-name vs Odds-API-full-name mismatch by checking whether
 * the shorter key is a word-boundary-aligned prefix of the longer one.
 * Examples:
 *   "duke"                 matches "duke blue devils"
 *   "iowa state"           matches "iowa state cyclones"
 *   "north carolina state" matches "north carolina state wolfpack"
 *   "saint johns"          matches "saint johns red storm"
 *   "umbc"                 matches "umbc retrievers"
 *
 * Both directions are checked so either side can be the shorter/longer name.
 */
const teamKeysMatch = (keyA: string, keyB: string): boolean => {
  if (keyA === keyB) return true;
  const [shorter, longer] = keyA.length <= keyB.length ? [keyA, keyB] : [keyB, keyA];
  // The next character after the prefix must be a space (word boundary), not a letter.
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
  const gameHomeKey = toCanonicalTeamKey(game.homeTeam);
  const gameAwayKey = toCanonicalTeamKey(game.awayTeam);
  const eventHomeKey = toCanonicalTeamKey(String(event.home_team ?? ""));
  const eventAwayKey = toCanonicalTeamKey(String(event.away_team ?? ""));
  return teamKeysMatch(gameHomeKey, eventHomeKey) && teamKeysMatch(gameAwayKey, eventAwayKey);
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
  // Filter to events that fall on the selected Eastern date.
  // The fetch window spans +2 days UTC to capture late-night games that tip after midnight UTC.
  const dateEvents = events.filter((event) => {
    const eventDate = event.commence_time ? toEasternIsoDate(event.commence_time) : null;
    return eventDate === selectedDate;
  });

  console.log(`[Vegas] ${dateEvents.length} odds events for ${selectedDate}, matching against ${games.length} games`);

  // Build exact-match map keyed by the Odds API canonical game key.
  // Exact matches are O(1) and cover cases where both sides already canonicalize identically.
  const eventByKey = new Map<string, OddsEvent>();
  for (const event of dateEvents) {
    const key = gameLookupKey(String(event.home_team ?? ""), String(event.away_team ?? ""));
    if (!eventByKey.has(key)) eventByKey.set(key, event);
  }

  const lookup = new Map<string, VegasInfo>();
  for (const game of games) {
    const espnKey = gameLookupKey(game.homeTeam, game.awayTeam);

    // 1. Try exact canonical key match (fast path)
    let matchedEvent = eventByKey.get(espnKey);

    // 2. Fall back to fuzzy prefix match.
    //    This resolves the ESPN-short-name vs Odds-API-full-name mismatch:
    //    e.g. ESPN "Duke" (→ "duke") vs Odds API "Duke Blue Devils" (→ "duke blue devils").
    if (!matchedEvent) {
      matchedEvent = dateEvents.find((candidate) => matchOddsEventToGame(selectedDate, game, candidate));
      if (matchedEvent) {
        const oddsKey = gameLookupKey(String(matchedEvent.home_team ?? ""), String(matchedEvent.away_team ?? ""));
        console.log(`[Vegas] Matched: ESPN '${espnKey}' -> Odds API '${oddsKey}'`);
      } else {
        console.log(`[Vegas] No odds match: '${espnKey}' (tried against ${dateEvents.length} events)`);
      }
    }

    lookup.set(espnKey, matchedEvent ? extractFanDuelLineFromEvent(matchedEvent) : lineUnavailable());
  }

  return lookup;
};

export const getGameLookupKey = (homeTeam: string, awayTeam: string): string => gameLookupKey(homeTeam, awayTeam);
