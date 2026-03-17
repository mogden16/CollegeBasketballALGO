import gamesData from "../data/games-by-date.json";
import modelsData from "../data/team-models.json";
import { TEAM_HOME_COORDINATES } from "./team-home-locations";

type Env = {
  ODDS_API_KEY?: string;
};

type ModelProjection = {
  homeScore: number | null;
  awayScore: number | null;
  total: number | null;
  spread: number | null;
  spreadEdge: number | null;
  totalEdge: number | null;
};

type LineProjection = {
  spread: number | null;
  total: number | null;
  bookmaker?: "fanduel";
  unavailableReason?: string;
};

type GamePrediction = {
  selectedDate: string;
  homeTeam: string;
  awayTeam: string;
  neutral: boolean;
  gameTimeUtc: string | null;
  gameTimeEtDisplay: string;
  awayLogo: string | null;
  homeLogo: string | null;
  kenpom: ModelProjection;
  trank: ModelProjection;
  vegas: LineProjection;
  projectedSpread: number | null;
  projectedTotal: number | null;
  fanduelSpread: number | null;
  fanduelTotal: number | null;
  edge: number | null;
  edgeSummary: string[];
  neutralSite: boolean;
  travelDistanceMiles: number | null;
  source: "cache" | "live";
  isEdge: boolean;
  confidence: string | null;
};

type PicksResponse = {
  selectedDate: string;
  picks: GamePrediction[];
  source: "cache" | "live";
  reason?: "no_games_scheduled" | "no_cached_data" | "upstream_unavailable";
};

type GamesByDatePayload = {
  dates: Record<string, Partial<GamePrediction>[]>;
};

type TeamRatings = {
  adjO: number;
  adjD: number;
  adjT: number;
  sourceName: string;
};

type TeamModelsPayload = {
  kenpom: Record<string, TeamRatings>;
  trank: Record<string, TeamRatings>;
  teams: string[];
};

type EspnMatchup = {
  homeTeam: string;
  awayTeam: string;
  neutral: boolean;
  gameTimeUtc: string | null;
  gameTimeEtDisplay: string;
  awayLogo: string | null;
  homeLogo: string | null;
};

type FanDuelMarket = { spread: number | null; total: number | null };

const payload = gamesData as GamesByDatePayload;
const teamModels = modelsData as TeamModelsPayload;

const jsonHeaders = { "content-type": "application/json; charset=utf-8" };
const toEtIsoDate = (d: Date): string => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
const todayEt = toEtIsoDate(new Date());

const minDate = new Date();
minDate.setFullYear(minDate.getFullYear() - 2);
const maxDate = new Date();
maxDate.setFullYear(maxDate.getFullYear() + 1);

const TEAM_ALIASES: Record<string, string> = {
  uconn: "Connecticut",
  "u conn": "Connecticut",
  unc: "North Carolina",
  "st johns": "St. John's",
  "st john": "St. John's",
  "saint johns": "St. John's",
  "saint john": "St. John's",
  "iowa st": "Iowa St.",
  "michigan st": "Michigan St.",
  "texas am": "Texas A&M",
};

const LAMBDA = 0.88;
const AVG_EFFICIENCY = 100;
const HCA = 3.5;

const normalizeTeamName = (input: string): string =>
  input
    .trim()
    .replace(/^\d+\s*/, "")
    .replace(/\s*\d+$/, "")
    .replace(/[’']/g, "")
    .replace(/[.&]/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();

const buildNormalizedLookup = (teams: string[]): Map<string, string> => {
  const lookup = new Map<string, string>();
  for (const team of teams) lookup.set(normalizeTeamName(team), team);
  return lookup;
};

const buildKey = (awayTeam: string, homeTeam: string) => `${normalizeTeamName(awayTeam)}|${normalizeTeamName(homeTeam)}`;

const kenpomLookup = buildNormalizedLookup(Object.keys(teamModels.kenpom));
const trankLookup = buildNormalizedLookup(Object.keys(teamModels.trank));

const deterministicFallback = (query: string, lookup: Map<string, string>): string | null => {
  if (query.split(" ").length < 2) return null;
  const candidates = [...lookup.keys()].filter((name) => name.startsWith(query) || query.startsWith(name));
  if (candidates.length !== 1) return null;
  return lookup.get(candidates[0]) ?? null;
};

const resolveTeamName = (teamName: string, lookup: Map<string, string>): string | null => {
  const normalized = normalizeTeamName(teamName);
  const exact = lookup.get(normalized);
  if (exact) return exact;

  const alias = TEAM_ALIASES[normalized];
  if (alias) {
    const aliasExact = lookup.get(normalizeTeamName(alias));
    if (aliasExact) return aliasExact;
  }

  return deterministicFallback(normalized, lookup);
};

const predictGame = (home: TeamRatings, away: TeamRatings, neutral = false) => {
  const tempo = (home.adjT + away.adjT) / 2;
  const effHome = home.adjO + LAMBDA * (away.adjD - AVG_EFFICIENCY);
  const effAway = away.adjO + LAMBDA * (home.adjD - AVG_EFFICIENCY);
  const homeScore = Number(((tempo * effHome) / 100).toFixed(1));
  const awayScore = Number(((tempo * effAway) / 100).toFixed(1));
  const spread = Number((-((homeScore - awayScore) + (neutral ? 0 : HCA))).toFixed(1));
  return { homeScore, awayScore, total: Number((homeScore + awayScore).toFixed(1)), spread };
};

const toNullableNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

const formatEtTime = (utc: string | null): string => {
  if (!utc) return "Time TBD";
  const d = new Date(utc);
  if (Number.isNaN(d.getTime())) return "Time TBD";
  return `${new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(d)} ET`;
};

const getGamesForDate = (selectedDate: string): Partial<GamePrediction>[] => payload.dates[selectedDate] ?? [];

const computeDistanceMiles = (lat1: number, lon1: number, lat2: number, lon2: number): number => {
  const R = 3958.8;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return Number((R * (2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)))).toFixed(0));
};

const getTravelNote = (awayTeam: string, homeTeam: string, neutral: boolean): { miles: number | null; note: string } => {
  if (neutral) return { miles: null, note: "Travel note: Neutral-site game; travel distance varies by venue." };
  const away = TEAM_HOME_COORDINATES[normalizeTeamName(awayTeam)];
  const home = TEAM_HOME_COORDINATES[normalizeTeamName(homeTeam)];
  if (!away || !home) return { miles: null, note: "Travel note: Distance unavailable for one or both teams." };
  const miles = computeDistanceMiles(away.lat, away.lon, home.lat, home.lon);
  if (miles < 120) return { miles, note: `Travel note: Minimal travel distance (${miles} miles).` };
  return { miles, note: `Travel note: Away team is ${miles.toLocaleString()} miles from home.` };
};

const parseSelectedDate = (raw: string | null): string => {
  if (!raw) return todayEt;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return todayEt;
  return raw;
};

const fetchEspnSchedule = async (selectedDate: string): Promise<EspnMatchup[] | null> => {
  const yyyymmdd = selectedDate.replaceAll("-", "");
  const url = `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=${yyyymmdd}&groups=50`;

  try {
    const response = await fetch(url, { cf: { cacheTtl: 120, cacheEverything: false } });
    if (!response.ok) return null;
    const data = (await response.json()) as Record<string, unknown>;
    const events = (data.events as Record<string, unknown>[] | undefined) ?? [];

    const matchups: EspnMatchup[] = [];
    for (const event of events) {
      const competitions = (event.competitions as Record<string, unknown>[] | undefined) ?? [];
      const comp = competitions[0] ?? {};
      const competitors = (comp.competitors as Record<string, unknown>[] | undefined) ?? [];
      if (competitors.length < 2) continue;

      let homeTeam = "";
      let awayTeam = "";
      let homeLogo: string | null = null;
      let awayLogo: string | null = null;

      for (const c of competitors) {
        const team = (c.team as Record<string, unknown> | undefined) ?? {};
        const teamName = String(team.shortDisplayName ?? team.displayName ?? "").trim();
        const logos = (team.logos as Record<string, unknown>[] | undefined) ?? [];
        const logo = logos.length ? String(logos[0].href ?? "") : "";
        if (String(c.homeAway ?? "") === "home") {
          homeTeam = teamName;
          homeLogo = logo || null;
        } else {
          awayTeam = teamName;
          awayLogo = logo || null;
        }
      }

      if (homeTeam && awayTeam) {
        const gameTimeUtc = typeof event.date === "string" ? event.date : null;
        matchups.push({
          homeTeam,
          awayTeam,
          neutral: Boolean(comp.neutralSite),
          gameTimeUtc,
          gameTimeEtDisplay: formatEtTime(gameTimeUtc),
          awayLogo,
          homeLogo,
        });
      }
    }

    return matchups;
  } catch {
    return null;
  }
};

const parseFanDuelMarket = (bookmakers: Record<string, unknown>[], homeTeam: string, awayTeam: string): FanDuelMarket => {
  const fanduel = bookmakers.find((book) => String(book.key ?? "") === "fanduel");
  if (!fanduel) return { spread: null, total: null };

  const markets = (fanduel.markets as Record<string, unknown>[] | undefined) ?? [];
  const spreads = markets.find((m) => String(m.key ?? "") === "spreads");
  const totals = markets.find((m) => String(m.key ?? "") === "totals");

  let spread: number | null = null;
  const spreadOutcomes = (spreads?.outcomes as Record<string, unknown>[] | undefined) ?? [];
  const homeSpread = spreadOutcomes.find((o) => normalizeTeamName(String(o.name ?? "")) === normalizeTeamName(homeTeam));
  const awaySpread = spreadOutcomes.find((o) => normalizeTeamName(String(o.name ?? "")) === normalizeTeamName(awayTeam));
  spread = toNullableNumber(homeSpread?.point);
  if (spread === null) {
    const awayPoint = toNullableNumber(awaySpread?.point);
    spread = awayPoint === null ? null : -awayPoint;
  }

  let total: number | null = null;
  const totalOutcomes = (totals?.outcomes as Record<string, unknown>[] | undefined) ?? [];
  const over = totalOutcomes.find((o) => String(o.name ?? "").toLowerCase() === "over");
  total = toNullableNumber(over?.point);
  if (total === null && totalOutcomes.length) total = toNullableNumber(totalOutcomes[0].point);

  return { spread, total };
};

const fetchFanDuelOdds = async (selectedDate: string, env: Env): Promise<Map<string, FanDuelMarket>> => {
  if (!env.ODDS_API_KEY) return new Map();
  const url = `https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds?apiKey=${encodeURIComponent(env.ODDS_API_KEY)}&regions=us&markets=spreads,totals&bookmakers=fanduel&oddsFormat=american&dateFormat=iso`;

  try {
    const response = await fetch(url, { cf: { cacheTtl: 120, cacheEverything: true } });
    if (!response.ok) return new Map();
    const data = (await response.json()) as Record<string, unknown>[];
    const byGame = new Map<string, FanDuelMarket>();

    for (const event of data) {
      const homeTeam = String(event.home_team ?? "");
      const awayTeam = String(event.away_team ?? "");
      const commence = String(event.commence_time ?? "");
      if (!homeTeam || !awayTeam) continue;
      if (toEtIsoDate(new Date(commence)) !== selectedDate) continue;

      const bookmakers = (event.bookmakers as Record<string, unknown>[] | undefined) ?? [];
      const market = parseFanDuelMarket(bookmakers, homeTeam, awayTeam);
      byGame.set(buildKey(awayTeam, homeTeam), market);
    }

    return byGame;
  } catch {
    return new Map();
  }
};

const calculateEdgeSummary = (game: GamePrediction): string[] => {
  const notes: string[] = [];
  if (game.kenpom.spreadEdge !== null) {
    notes.push(`KenPom spread edge: ${game.kenpom.spreadEdge > 0 ? "Home" : "Away"} +${Math.abs(game.kenpom.spreadEdge).toFixed(1)}`);
  }
  if (game.kenpom.totalEdge !== null) notes.push(`KenPom total edge: ${game.kenpom.totalEdge > 0 ? "Over" : "Under"} ${Math.abs(game.kenpom.totalEdge).toFixed(1)}`);
  if (game.trank.spreadEdge !== null) {
    notes.push(`T-Rank spread edge: ${game.trank.spreadEdge > 0 ? "Home" : "Away"} +${Math.abs(game.trank.spreadEdge).toFixed(1)}`);
  }
  if (game.trank.totalEdge !== null) notes.push(`T-Rank total edge: ${game.trank.totalEdge > 0 ? "Over" : "Under"} ${Math.abs(game.trank.totalEdge).toFixed(1)}`);
  return [...notes, game.edgeSummary[game.edgeSummary.length - 1]];
};

const hydrateGame = (
  selectedDate: string,
  base: Partial<GamePrediction>,
  source: "cache" | "live",
  espnMatch: EspnMatchup | null,
  odds: FanDuelMarket | null,
): GamePrediction => {
  const awayTeam = String(base.awayTeam ?? espnMatch?.awayTeam ?? "Away Team");
  const homeTeam = String(base.homeTeam ?? espnMatch?.homeTeam ?? "Home Team");
  const neutral = Boolean(base.neutral ?? espnMatch?.neutral ?? false);

  const kenpom = (base.kenpom as ModelProjection | undefined) ?? {
    homeScore: null,
    awayScore: null,
    total: null,
    spread: null,
    spreadEdge: null,
    totalEdge: null,
  };
  const trank = (base.trank as ModelProjection | undefined) ?? {
    homeScore: null,
    awayScore: null,
    total: null,
    spread: null,
    spreadEdge: null,
    totalEdge: null,
  };

  const modelSpread = kenpom.spread ?? trank.spread ?? null;
  const modelTotal = kenpom.total ?? trank.total ?? null;
  const fanduelSpread = odds?.spread ?? toNullableNumber((base.vegas as LineProjection | undefined)?.spread);
  const fanduelTotal = odds?.total ?? toNullableNumber((base.vegas as LineProjection | undefined)?.total);

  if (fanduelSpread !== null) {
    if (kenpom.spread !== null) kenpom.spreadEdge = Number((fanduelSpread - kenpom.spread).toFixed(1));
    if (trank.spread !== null) trank.spreadEdge = Number((fanduelSpread - trank.spread).toFixed(1));
  }
  if (fanduelTotal !== null) {
    if (kenpom.total !== null) kenpom.totalEdge = Number((kenpom.total - fanduelTotal).toFixed(1));
    if (trank.total !== null) trank.totalEdge = Number((trank.total - fanduelTotal).toFixed(1));
  }

  const travel = getTravelNote(awayTeam, homeTeam, neutral);
  const fanDuelNote =
    fanduelSpread === null && fanduelTotal === null
      ? "FanDuel line unavailable"
      : `FanDuel spread ${fanduelSpread ?? "N/A"}, total ${fanduelTotal ?? "N/A"}`;

  const game: GamePrediction = {
    selectedDate,
    homeTeam,
    awayTeam,
    neutral,
    gameTimeUtc: espnMatch?.gameTimeUtc ?? (typeof base.gameTimeUtc === "string" ? base.gameTimeUtc : null),
    gameTimeEtDisplay: espnMatch?.gameTimeEtDisplay ?? formatEtTime(typeof base.gameTimeUtc === "string" ? base.gameTimeUtc : null),
    awayLogo: espnMatch?.awayLogo ?? (typeof base.awayLogo === "string" ? base.awayLogo : null),
    homeLogo: espnMatch?.homeLogo ?? (typeof base.homeLogo === "string" ? base.homeLogo : null),
    kenpom,
    trank,
    vegas: {
      spread: fanduelSpread,
      total: fanduelTotal,
      bookmaker: fanduelSpread === null && fanduelTotal === null ? undefined : "fanduel",
      unavailableReason: fanduelSpread === null && fanduelTotal === null ? "FanDuel line unavailable" : undefined,
    },
    projectedSpread: modelSpread,
    projectedTotal: modelTotal,
    fanduelSpread,
    fanduelTotal,
    edge: modelSpread !== null && fanduelSpread !== null ? Number((fanduelSpread - modelSpread).toFixed(1)) : null,
    edgeSummary: [fanDuelNote, travel.note],
    neutralSite: neutral,
    travelDistanceMiles: travel.miles,
    source,
    isEdge: Boolean(base.isEdge ?? false),
    confidence: (base.confidence as string | null | undefined) ?? null,
  };

  game.edgeSummary = calculateEdgeSummary(game);
  return game;
};

const buildLiveGamesForDate = async (selectedDate: string, env: Env): Promise<PicksResponse> => {
  const schedule = await fetchEspnSchedule(selectedDate);
  if (schedule === null) return { selectedDate, picks: [], source: "live", reason: "upstream_unavailable" };

  const fanDuelByGame = await fetchFanDuelOdds(selectedDate, env);
  const picks: GamePrediction[] = [];

  for (const game of schedule) {
    const resolvedHomeKenpom = resolveTeamName(game.homeTeam, kenpomLookup);
    const resolvedAwayKenpom = resolveTeamName(game.awayTeam, kenpomLookup);
    const resolvedHomeTrank = resolveTeamName(game.homeTeam, trankLookup);
    const resolvedAwayTrank = resolveTeamName(game.awayTeam, trankLookup);

    const base: Partial<GamePrediction> = {
      homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? game.homeTeam,
      awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? game.awayTeam,
      neutral: game.neutral,
      kenpom: { homeScore: null, awayScore: null, total: null, spread: null, spreadEdge: null, totalEdge: null },
      trank: { homeScore: null, awayScore: null, total: null, spread: null, spreadEdge: null, totalEdge: null },
      isEdge: false,
      confidence: null,
    };

    if (resolvedHomeKenpom && resolvedAwayKenpom) {
      base.kenpom = { ...base.kenpom, ...predictGame(teamModels.kenpom[resolvedHomeKenpom], teamModels.kenpom[resolvedAwayKenpom], game.neutral) } as ModelProjection;
    }
    if (resolvedHomeTrank && resolvedAwayTrank) {
      base.trank = { ...base.trank, ...predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], game.neutral) } as ModelProjection;
    }

    picks.push(hydrateGame(selectedDate, base, "live", game, fanDuelByGame.get(buildKey(game.awayTeam, game.homeTeam)) ?? null));
  }

  return { selectedDate, picks, source: "live", reason: picks.length ? undefined : "no_games_scheduled" };
};

const getPicksResponse = async (selectedDate: string, env: Env): Promise<PicksResponse> => {
  const cached = getGamesForDate(selectedDate);
  const schedule = await fetchEspnSchedule(selectedDate);
  const fanDuelByGame = await fetchFanDuelOdds(selectedDate, env);
  const scheduleByKey = new Map((schedule ?? []).map((g) => [buildKey(g.awayTeam, g.homeTeam), g]));

  if (cached.length > 0) {
    return {
      selectedDate,
      picks: cached.map((game) => {
        const key = buildKey(String(game.awayTeam ?? ""), String(game.homeTeam ?? ""));
        return hydrateGame(selectedDate, game, "cache", scheduleByKey.get(key) ?? null, fanDuelByGame.get(key) ?? null);
      }),
      source: "cache",
    };
  }

  const liveResult = await buildLiveGamesForDate(selectedDate, env);
  if (liveResult.picks.length > 0 || liveResult.reason === "upstream_unavailable") return liveResult;
  return { selectedDate, picks: [], source: "cache", reason: "no_cached_data" };
};

const renderHomePage = () => `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>College Basketball Dashboard</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body { margin: 0; font-family: Inter, system-ui, sans-serif; background:#050b18; color:#e2e8f0; font-size:14px; }
      main { max-width: 1200px; margin: 0 auto; padding: .85rem; }
      .hero { position: sticky; top: 0; z-index: 3; background:#050b18; padding-bottom:.55rem; margin-bottom:.55rem; border-bottom:1px solid #1f2937; }
      h1 { margin: 0; font-size:1.2rem; }
      .subtitle { margin:.2rem 0 0; color:#94a3b8; font-size:.8rem; }
      .layout { display:grid; gap:.7rem; grid-template-columns: minmax(0,1fr) 250px; align-items:start; }
      .controls { position: sticky; top: 3.2rem; background:#0b1220; border:1px solid #243044; border-radius:10px; padding:.65rem; }
      .controls h3 { margin:.05rem 0 .5rem; font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; }
      .control-group { margin-bottom:.55rem; }
      .control-group label { display:block; font-size:.68rem; text-transform:uppercase; color:#cbd5e1; margin-bottom:.18rem; }
      input, select, button { width:100%; padding:.44rem .5rem; border-radius:8px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; font-size:.82rem; }
      .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:.55rem; }
      .card { background:#0b1324; border:1px solid #1f314d; border-radius:10px; padding:.62rem; box-shadow: 0 4px 16px rgba(0,0,0,.18); }
      .card.highlight { border-color:#f59e0b; }
      .matchup { display:flex; align-items:center; justify-content:space-between; gap:.4rem; font-size:.9rem; margin:0 0 .18rem; }
      .teams { display:flex; align-items:center; gap:.34rem; min-width:0; }
      .logo { width:18px; height:18px; object-fit:contain; border-radius:50%; background:#0f172a; }
      .team { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .line { font-size:.76rem; color:#cbd5e1; margin:.1rem 0; }
      .edge { margin-top:.38rem; border:1px solid #334155; border-radius:7px; padding:.35rem .45rem; font-size:.74rem; line-height:1.35; }
      .panel { margin-top:.75rem; background:#0b1220; border:1px solid #243044; border-radius:10px; padding:.65rem; }
      .quick-grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap:.5rem; }
      .small-note { color:#94a3b8; font-size:.74rem; margin-top:.4rem; }
      @media (max-width: 880px) { .layout { grid-template-columns: 1fr; } .controls { position: static; } .hero { position: static; } }
    </style>
  </head>
  <body>
    <main>
      <header class="hero"><h1>Picks of the Day</h1><p class="subtitle">Compact model dashboard with FanDuel-only lines and ET start times.</p></header>
      <div class="layout">
        <section>
          <section id="cards" class="grid"></section>
          <p id="empty-state" class="small-note" style="display:none;"></p>
          <section class="panel">
            <h2 style="margin:0 0 .45rem;font-size:.95rem;">Quick Predict</h2>
            <form id="quick-form"><div class="quick-grid">
              <label>Home Team<input list="team-list" type="text" name="homeTeam" required /></label>
              <label>Away Team<input list="team-list" type="text" name="awayTeam" required /></label>
              <label>Neutral?<select name="neutral"><option value="false">No</option><option value="true">Yes</option></select></label>
              <div><button type="submit">Run</button></div>
            </div><datalist id="team-list"></datalist></form>
            <div id="quick-result"></div>
          </section>
        </section>
        <aside class="controls">
          <h3>Filters</h3>
          <div class="control-group"><label for="pick-date">Selected Date</label><input id="pick-date" type="date" value="${todayEt}" min="${toEtIsoDate(minDate)}" max="${toEtIsoDate(maxDate)}" /></div>
          <div class="control-group"><label for="spread-threshold">Spread discrepancy</label><input id="spread-threshold" type="number" value="3" step="0.5" min="0" /></div>
          <div class="control-group"><label for="total-threshold">Total discrepancy</label><input id="total-threshold" type="number" value="5" step="0.5" min="0" /></div>
          <p class="small-note" id="status-line">Date: ${todayEt}</p>
        </aside>
      </div>
    </main>
    <script>
      const cardsContainer = document.getElementById('cards');
      const dateInput = document.getElementById('pick-date');
      const spreadThresholdInput = document.getElementById('spread-threshold');
      const totalThresholdInput = document.getElementById('total-threshold');
      const statusLine = document.getElementById('status-line');
      const emptyState = document.getElementById('empty-state');
      const teamList = document.getElementById('team-list');
      const quickForm = document.getElementById('quick-form');
      const quickResult = document.getElementById('quick-result');
      let currentGames = []; let currentSource = 'cache'; let currentReason = '';

      const formatSpread = (spread) => spread == null ? 'N/A' : (spread < 0 ? 'Home ' + spread : 'Away +' + spread);
      const modelHasDiscrepancy = (model, spreadThreshold, totalThreshold) => model && model.spreadEdge != null && model.totalEdge != null && Math.abs(model.spreadEdge) >= spreadThreshold && Math.abs(model.totalEdge) >= totalThreshold;
      const logoOrBlank = (src, alt) => src ? '<img class="logo" src="' + src + '" alt="' + alt + ' logo" />' : '<span class="logo" aria-hidden="true"></span>';

      function renderCards(games) {
        const spreadThreshold = Number(spreadThresholdInput.value) || 0;
        const totalThreshold = Number(totalThresholdInput.value) || 0;
        let highlighted = 0;

        cardsContainer.innerHTML = games.map((game) => {
          const shouldHighlight = modelHasDiscrepancy(game.kenpom, spreadThreshold, totalThreshold) || modelHasDiscrepancy(game.trank, spreadThreshold, totalThreshold);
          if (shouldHighlight) highlighted += 1;
          const edgeRows = (game.edgeSummary && game.edgeSummary.length ? game.edgeSummary : ['No actionable edge from available model + line data.']).map((row) => '<div>- ' + row + '</div>').join('');
          return '<article class="card' + (shouldHighlight ? ' highlight' : '') + '">' +
            '<h2 class="matchup">' +
              '<span class="teams">' + logoOrBlank(game.awayLogo, game.awayTeam) + '<span class="team">' + game.awayTeam + '</span> at ' + logoOrBlank(game.homeLogo, game.homeTeam) + '<span class="team">' + game.homeTeam + '</span></span>' +
              (game.confidence ? '<span>' + game.confidence + '</span>' : '') +
            '</h2>' +
            '<div class="line">Time: ' + (game.gameTimeEtDisplay || 'Time TBD') + (game.neutralSite ? ' • Neutral site' : '') + '</div>' +
            '<div class="line">Model: Spread ' + formatSpread(game.projectedSpread) + ' | Total ' + (game.projectedTotal ?? 'N/A') + '</div>' +
            '<div class="line">FanDuel: Spread ' + formatSpread(game.fanduelSpread) + ' | Total ' + (game.fanduelTotal ?? 'N/A') + '</div>' +
            '<div class="edge"><strong>EDGE SUMMARY</strong>' + edgeRows + '</div>' +
          '</article>';
        }).join('');

        const reasonText = currentReason ? ' • Reason: ' + currentReason : '';
        statusLine.textContent = 'Date: ' + dateInput.value + ' • Source: ' + currentSource + ' • Games: ' + games.length + ' • Highlighted: ' + highlighted + reasonText;

        if (!currentGames.length) {
          emptyState.textContent = currentReason === 'upstream_unavailable' ? 'No games shown: upstream schedule unavailable.' : currentReason === 'no_cached_data' ? 'No games scheduled / no data for today.' : 'No games scheduled for this date.';
          emptyState.style.display = 'block';
        } else {
          emptyState.style.display = 'none';
        }
      }

      async function loadTeams() { const response = await fetch('/api/teams'); const data = await response.json(); teamList.innerHTML = data.teams.map((team) => '<option value="' + team + '"></option>').join(''); }
      async function loadPicks() {
        const response = await fetch('/api/picks?date=' + encodeURIComponent(dateInput.value));
        const data = await response.json();
        currentGames = data.picks || []; currentSource = data.source || 'cache'; currentReason = data.reason || ''; renderCards(currentGames);
      }

      dateInput.addEventListener('change', loadPicks); dateInput.addEventListener('input', loadPicks);
      spreadThresholdInput.addEventListener('input', () => renderCards(currentGames));
      totalThresholdInput.addEventListener('input', () => renderCards(currentGames));

      quickForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const body = Object.fromEntries(new FormData(quickForm).entries());
        const response = await fetch('/api/quick-predict', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
        const data = await response.json();
        quickResult.innerHTML = '<div class="edge"><div><strong>' + data.awayTeam + ' at ' + data.homeTeam + '</strong></div>' +
          (data.kenpom ? '<div>KenPom: Spread ' + data.kenpom.spread + ' | Total ' + data.kenpom.total + '</div>' : '') +
          (data.trank ? '<div>T-Rank: Spread ' + data.trank.spread + ' | Total ' + data.trank.total + '</div>' : '') +
          ((data.notes || []).map((n) => '<div>' + n + '</div>').join('')) + '</div>';
      });

      if (!dateInput.value) dateInput.value = '${todayEt}';
      loadTeams(); loadPicks();
    </script>
  </body>
</html>`;

const quickPredict = async (request: Request) => {
  const body = (await request.json()) as Record<string, string>;
  const homeTeamInput = String(body.homeTeam || "").trim();
  const awayTeamInput = String(body.awayTeam || "").trim();
  const neutral = String(body.neutral || "false") === "true";

  if (!homeTeamInput || !awayTeamInput) {
    return new Response(JSON.stringify({ error: "homeTeam and awayTeam are required." }), { status: 400, headers: jsonHeaders });
  }

  const resolvedHomeKenpom = resolveTeamName(homeTeamInput, kenpomLookup);
  const resolvedAwayKenpom = resolveTeamName(awayTeamInput, kenpomLookup);
  const resolvedHomeTrank = resolveTeamName(homeTeamInput, trankLookup);
  const resolvedAwayTrank = resolveTeamName(awayTeamInput, trankLookup);

  const notes: string[] = [];
  let kenpomResult: ReturnType<typeof predictGame> | null = null;
  let trankResult: ReturnType<typeof predictGame> | null = null;

  if (resolvedHomeKenpom && resolvedAwayKenpom) {
    kenpomResult = predictGame(teamModels.kenpom[resolvedHomeKenpom], teamModels.kenpom[resolvedAwayKenpom], neutral);
  } else notes.push("KenPom projection unavailable for one or both teams.");

  if (resolvedHomeTrank && resolvedAwayTrank) {
    trankResult = predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], neutral);
  } else notes.push("T-Rank projection unavailable for one or both teams.");

  return new Response(JSON.stringify({
    homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? homeTeamInput,
    awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? awayTeamInput,
    kenpom: kenpomResult,
    trank: trankResult,
    notes,
  }), { headers: jsonHeaders });
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/api/picks") {
      const selectedDate = parseSelectedDate(url.searchParams.get("date"));
      const result = await getPicksResponse(selectedDate, env);
      return new Response(JSON.stringify(result), { headers: jsonHeaders });
    }

    if (request.method === "GET" && url.pathname === "/api/dates") {
      return new Response(JSON.stringify({ dates: Object.keys(payload.dates).sort() }), { headers: jsonHeaders });
    }

    if (request.method === "GET" && url.pathname === "/api/teams") {
      return new Response(JSON.stringify({ teams: teamModels.teams }), { headers: jsonHeaders });
    }

    if (request.method === "POST" && url.pathname === "/api/quick-predict") return quickPredict(request);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response(renderHomePage(), { headers: { "content-type": "text/html; charset=utf-8" } });
    }

    return new Response("Not found", { status: 404 });
  },
};
