import gamesData from "../data/games-by-date.json";
import modelsData from "../data/team-models.json";
import { normalizeTeamName } from "./teamName";
import { toLocalIsoDate } from "./date";
import { buildFanDuelLookupForDate, fetchOddsApiEventsForDate, getGameLookupKey, type VegasInfo } from "./odds";

type ModelProjection = {
  homeScore: number | null;
  awayScore: number | null;
  total: number | null;
  spread: number | null;
  spreadEdge: number | null;
  totalEdge: number | null;
};

type DistanceInfo = { homeMiles: number | null; awayMiles: number | null };

type GamePrediction = {
  homeTeam: string;
  awayTeam: string;
  neutral: boolean;
  gameTimeEt?: string | null;
  kenpom: ModelProjection;
  trank: ModelProjection;
  vegas: VegasInfo;
  distance?: DistanceInfo | null;
  isEdge: boolean;
  confidence: string | null;
};

type PicksResponse = {
  selectedDate: string;
  picks: GamePrediction[];
  source: "cache" | "live";
  reason?: "no_games_scheduled" | "no_cached_data" | "upstream_unavailable";
};

type TeamRatings = { adjO: number; adjD: number; adjT: number; sourceName: string };
type TeamModelsPayload = { kenpom: Record<string, TeamRatings>; trank: Record<string, TeamRatings>; teams: string[] };
type GamesByDatePayload = { dates: Record<string, GamePrediction[]> };
type EspnMatchup = { homeTeam: string; awayTeam: string; neutral: boolean; gameTimeEt: string | null };
type Env = { ODDS_API_KEY?: string };
type UpstreamTestResult = {
  name: "espn" | "odds";
  ok: boolean;
  status: number | null;
  statusText: string;
  durationMs: number;
  details: string;
};

const payload = gamesData as GamesByDatePayload;
const teamModels = modelsData as TeamModelsPayload;
const jsonHeaders = { "content-type": "application/json; charset=utf-8" };

const todayDate = new Date();
const today = toLocalIsoDate(todayDate);
const minDate = new Date(todayDate);
minDate.setFullYear(minDate.getFullYear() - 2);
const maxDate = new Date(todayDate);
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

const buildNormalizedLookup = (teams: string[]): Map<string, string> => {
  const lookup = new Map<string, string>();
  for (const team of teams) lookup.set(normalizeTeamName(team), team);
  return lookup;
};

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

const toEasternTime = (isoDateTime: unknown): string | null => {
  if (typeof isoDateTime !== "string" || !isoDateTime) return null;
  const dt = new Date(isoDateTime);
  if (Number.isNaN(dt.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
  }).format(dt) + " ET";
};

const getGamesForDate = (selectedDate: string): GamePrediction[] => payload.dates[selectedDate] ?? [];

const getEspnScoreboardUrl = (selectedDate: string): string => {
  const yyyymmdd = selectedDate.replaceAll("-", "");
  return `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=${yyyymmdd}&groups=50`;
};

const fetchEspnSchedule = async (selectedDate: string): Promise<EspnMatchup[] | null> => {
  const url = getEspnScoreboardUrl(selectedDate);
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
      for (const c of competitors) {
        const team = (c.team as Record<string, unknown> | undefined) ?? {};
        const teamName = String(team.shortDisplayName ?? team.displayName ?? "").trim();
        if (String(c.homeAway ?? "") === "home") homeTeam = teamName;
        else awayTeam = teamName;
      }
      if (!homeTeam || !awayTeam) continue;

      matchups.push({
        homeTeam,
        awayTeam,
        neutral: Boolean(comp.neutralSite),
        gameTimeEt: toEasternTime(event.date),
      });
    }
    return matchups;
  } catch {
    return null;
  }
};

const testEspnApi = async (selectedDate: string): Promise<UpstreamTestResult> => {
  const url = getEspnScoreboardUrl(selectedDate);
  const startedAt = Date.now();
  try {
    const response = await fetch(url, { cf: { cacheTtl: 0, cacheEverything: false } });
    const durationMs = Date.now() - startedAt;
    let details = 'scoreboard fetch succeeded';
    if (response.ok) {
      const data = (await response.json()) as Record<string, unknown>;
      const events = Array.isArray(data.events) ? data.events : [];
      details = `${events.length} event(s) returned for ${selectedDate}`;
    }
    return {
      name: 'espn',
      ok: response.ok,
      status: response.status,
      statusText: response.statusText || (response.ok ? 'OK' : 'Request failed'),
      durationMs,
      details,
    };
  } catch (error) {
    return {
      name: 'espn',
      ok: false,
      status: null,
      statusText: 'Request failed',
      durationMs: Date.now() - startedAt,
      details: error instanceof Error ? error.message : 'Unknown error',
    };
  }
};

const testOddsApi = async (selectedDate: string, env: Env): Promise<UpstreamTestResult> => {
  const startedAt = Date.now();
  if (!env.ODDS_API_KEY) {
    return {
      name: 'odds',
      ok: false,
      status: null,
      statusText: 'Missing API key',
      durationMs: 0,
      details: 'ODDS_API_KEY is not configured in this environment.',
    };
  }

  const start = `${selectedDate}T00:00:00Z`;
  const dayEnd = new Date(`${selectedDate}T00:00:00Z`);
  dayEnd.setUTCDate(dayEnd.getUTCDate() + 2);
  const end = `${dayEnd.toISOString().slice(0, 10)}T06:00:00Z`;
  const url = new URL('https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds');
  url.searchParams.set('apiKey', env.ODDS_API_KEY);
  url.searchParams.set('regions', 'us');
  url.searchParams.set('markets', 'spreads,totals');
  url.searchParams.set('bookmakers', 'fanduel');
  url.searchParams.set('oddsFormat', 'american');
  url.searchParams.set('dateFormat', 'iso');
  url.searchParams.set('commenceTimeFrom', start);
  url.searchParams.set('commenceTimeTo', end);

  try {
    const response = await fetch(url.toString(), { cf: { cacheTtl: 0, cacheEverything: false } });
    const durationMs = Date.now() - startedAt;
    let details = 'odds fetch completed';
    if (response.ok) {
      const data = (await response.json()) as unknown;
      const events = Array.isArray(data) ? data : [];
      const remaining = response.headers.get('x-requests-remaining');
      const used = response.headers.get('x-requests-used');
      const quotaText = remaining || used ? ` • quota remaining=${remaining ?? 'n/a'}, used=${used ?? 'n/a'}` : '';
      details = `${events.length} event(s) returned for ${selectedDate}${quotaText}`;
    }
    return {
      name: 'odds',
      ok: response.ok,
      status: response.status,
      statusText: response.statusText || (response.ok ? 'OK' : 'Request failed'),
      durationMs,
      details,
    };
  } catch (error) {
    return {
      name: 'odds',
      ok: false,
      status: null,
      statusText: 'Request failed',
      durationMs: Date.now() - startedAt,
      details: error instanceof Error ? error.message : 'Unknown error',
    };
  }
};

const recalculateEdges = (pick: GamePrediction): GamePrediction => {
  const next = structuredClone(pick);
  next.kenpom.spreadEdge = next.vegas.spread !== null && next.kenpom.spread !== null ? Number((next.vegas.spread - next.kenpom.spread).toFixed(1)) : null;
  next.trank.spreadEdge = next.vegas.spread !== null && next.trank.spread !== null ? Number((next.vegas.spread - next.trank.spread).toFixed(1)) : null;
  next.kenpom.totalEdge = next.vegas.total !== null && next.kenpom.total !== null ? Number((next.kenpom.total - next.vegas.total).toFixed(1)) : null;
  next.trank.totalEdge = next.vegas.total !== null && next.trank.total !== null ? Number((next.trank.total - next.vegas.total).toFixed(1)) : null;
  return next;
};

const hydrateVegasForDate = async (selectedDate: string, picks: GamePrediction[], env: Env, onlyMissing: boolean): Promise<GamePrediction[]> => {
  if (picks.length === 0) return picks;
  if (!env.ODDS_API_KEY) {
    return picks.map((pick) =>
      recalculateEdges({
        ...pick,
        vegas: {
          spread: pick.vegas?.spread ?? null,
          total: pick.vegas?.total ?? null,
          vegasSource: "fanduel",
          vegasStatus: pick.vegas?.spread !== null && pick.vegas?.total !== null ? "available" : "unavailable",
        },
      }),
    );
  }

  const needsHydration = picks.some((pick) => pick.vegas?.spread === null || pick.vegas?.total === null);
  if (onlyMissing && !needsHydration) {
    return picks.map((pick) => recalculateEdges({ ...pick, vegas: { ...pick.vegas, vegasSource: "fanduel", vegasStatus: "available" } }));
  }

  const oddsEvents = await fetchOddsApiEventsForDate(selectedDate, env.ODDS_API_KEY);
  const oddsLookup = buildFanDuelLookupForDate(selectedDate, picks, oddsEvents);

  return picks.map((pick) => {
    const key = getGameLookupKey(pick.homeTeam, pick.awayTeam);
    const fromOdds = oddsLookup.get(key);
    if (!fromOdds) return recalculateEdges({ ...pick, vegas: { ...pick.vegas, vegasSource: "fanduel", vegasStatus: "unavailable" } });

    const spread = onlyMissing && pick.vegas.spread !== null ? pick.vegas.spread : fromOdds.spread;
    const total = onlyMissing && pick.vegas.total !== null ? pick.vegas.total : fromOdds.total;
    return recalculateEdges({
      ...pick,
      vegas: {
        spread,
        total,
        vegasSource: "fanduel",
        vegasStatus: spread !== null && total !== null ? "available" : "unavailable",
      },
    });
  });
};

const buildLiveGamesForDate = async (selectedDate: string, env: Env): Promise<PicksResponse> => {
  const schedule = await fetchEspnSchedule(selectedDate);
  if (schedule === null) return { selectedDate, picks: [], source: "live", reason: "upstream_unavailable" };
  const picks: GamePrediction[] = [];
  for (const game of schedule) {
    const resolvedHomeKenpom = resolveTeamName(game.homeTeam, kenpomLookup);
    const resolvedAwayKenpom = resolveTeamName(game.awayTeam, kenpomLookup);
    const resolvedHomeTrank = resolveTeamName(game.homeTeam, trankLookup);
    const resolvedAwayTrank = resolveTeamName(game.awayTeam, trankLookup);

    const kenpomProjection: ModelProjection = { homeScore: null, awayScore: null, total: null, spread: null, spreadEdge: null, totalEdge: null };
    const trankProjection: ModelProjection = { homeScore: null, awayScore: null, total: null, spread: null, spreadEdge: null, totalEdge: null };

    if (resolvedHomeKenpom && resolvedAwayKenpom) Object.assign(kenpomProjection, predictGame(teamModels.kenpom[resolvedHomeKenpom], teamModels.kenpom[resolvedAwayKenpom], game.neutral));
    if (resolvedHomeTrank && resolvedAwayTrank) Object.assign(trankProjection, predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], game.neutral));

    picks.push({
      homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? game.homeTeam,
      awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? game.awayTeam,
      neutral: game.neutral,
      gameTimeEt: game.gameTimeEt,
      kenpom: kenpomProjection,
      trank: trankProjection,
      vegas: { spread: null, total: null, vegasSource: "fanduel", vegasStatus: "unavailable" },
      isEdge: false,
      confidence: null,
    });
  }

  const withOdds = await hydrateVegasForDate(selectedDate, picks, env, false);

  for (const game of withOdds) {
    if (game.vegas.vegasStatus === "unavailable") {
      console.log(`FanDuel odds unavailable: ${selectedDate} ${game.awayTeam} @ ${game.homeTeam}`);
    }
  }

  return { selectedDate, picks: withOdds, source: "live", reason: withOdds.length ? undefined : "no_games_scheduled" };
};

const getPicksResponse = async (selectedDate: string, env: Env): Promise<PicksResponse> => {
  const cached = getGamesForDate(selectedDate);
  if (cached.length > 0) {
    const hydratedCached = await hydrateVegasForDate(selectedDate, cached, env, true);
    return { selectedDate, picks: hydratedCached, source: "cache" };
  }
  const liveResult = await buildLiveGamesForDate(selectedDate, env);
  if (liveResult.picks.length > 0 || liveResult.reason === "upstream_unavailable") return liveResult;
  return { selectedDate, picks: [], source: "cache", reason: "no_cached_data" };
};

const renderHomePage = () => `<!doctype html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>College Basketball Dashboard</title>
<style>
:root{color-scheme:dark;}*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;background:#020617;color:#e2e8f0}main{max-width:1260px;margin:0 auto;padding:1rem}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:1rem}.controls{position:sticky;top:1rem;background:#0b1220;border:1px solid #1f314d;border-radius:12px;padding:.9rem}.content{min-width:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}.card{background:#0b1220;border:1px solid #1f314d;border-radius:14px;padding:.9rem}.card.highlight{border-color:#f59e0b;box-shadow:0 0 0 1px rgba(245,158,11,.45)}
.line1{font-size:1rem;font-weight:700;margin:0 0 .7rem}.row{display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap;border-top:1px solid #1e293b;padding-top:.5rem;margin-top:.5rem}.label{color:#93c5fd;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}
.edgeBox{margin-top:.4rem;padding:.45rem .55rem;border-radius:10px;border:1px solid #334155;background:#111827}.edgeBox.hot{border-color:#14532d;background:rgba(22,101,52,.2);color:#dcfce7;font-weight:700}
.control-group{margin-bottom:.7rem}.control-group label{display:block;font-size:.78rem;text-transform:uppercase;margin-bottom:.2rem}input,button{width:100%;padding:.58rem .66rem;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}
.btn-row{display:flex;gap:.5rem}button{background:#22c55e;border-color:#22c55e;color:#052e16;font-weight:700;cursor:pointer}.today{background:#0f172a;color:#93c5fd;border-color:#334155}
.api-status{margin:.4rem 0 0;font-size:.82rem;color:#cbd5e1;word-break:break-word}
@media (max-width:980px){.layout{grid-template-columns:1fr}.controls{position:static;order:-1}}
</style></head><body><main><h1>Picks of the Day</h1><div class="layout"><aside class="controls"><h3>Filters</h3><div class="control-group"><label for="pick-date">Selected Date</label><input id="pick-date" type="date" value="${today}" min="${toLocalIsoDate(minDate)}" max="${toLocalIsoDate(maxDate)}"/></div><div class="btn-row"><button id="today-btn" type="button" class="today">Today</button><button id="refresh-btn" type="button">Refresh</button></div><div class="control-group"><button id="test-upstreams-btn" type="button">Test ESPN + Odds APIs</button></div><div id="api-status" class="api-status">Upstream API status: not tested yet.</div><div id="espn-status" class="api-status">ESPN: not tested yet.</div><div id="odds-status" class="api-status">The Odds API: not tested yet.</div><div class="control-group"><label for="spread-threshold">Spread threshold</label><input id="spread-threshold" type="number" value="3" step="0.5" min="0"/></div><div class="control-group"><label for="total-threshold">Total threshold</label><input id="total-threshold" type="number" value="5" step="0.5" min="0"/></div><p id="status-line"></p></aside><section class="content"><section id="cards" class="grid"></section><p id="empty-state" style="display:none;color:#94a3b8"></p></section></div></main>
<script>
const cardsContainer=document.getElementById('cards');const dateInput=document.getElementById('pick-date');const spreadThresholdInput=document.getElementById('spread-threshold');const totalThresholdInput=document.getElementById('total-threshold');const statusLine=document.getElementById('status-line');const emptyState=document.getElementById('empty-state');const apiStatus=document.getElementById('api-status');const espnStatus=document.getElementById('espn-status');const oddsStatus=document.getElementById('odds-status');
let currentGames=[];let currentSource='cache';let currentReason='';
const localToday=()=>{const d=new Date();d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,10)};
const formatSpread=(spread)=>spread==null?'N/A':(spread<0?'Home ':'Away +')+spread;
const modelScore=(m,away,home)=>m.awayScore==null||m.homeScore==null?'N/A':away+' '+m.awayScore+' - '+home+' '+m.homeScore;
const sideSignal=(edge,t)=>edge==null||Math.abs(edge)<t?null:(edge>0?'home':'away');
const totalSignal=(edge,t)=>edge==null||Math.abs(edge)<t?null:(edge>0?'over':'under');
function calcSideEdge(game,t){const pts={home:0,away:0};const reasons=[];const kp=sideSignal(game.kenpom.spreadEdge,t);if(kp){pts[kp]++;reasons.push('KenPom→'+kp)}const tr=sideSignal(game.trank.spreadEdge,t);if(tr){pts[tr]++;reasons.push('T-Rank→'+tr)}if(game.distance&&game.distance.homeMiles!=null&&game.distance.awayMiles!=null&&game.distance.homeMiles!==game.distance.awayMiles){const c=game.distance.homeMiles<game.distance.awayMiles?'home':'away';pts[c]++;reasons.push('Distance→'+c)}const side=pts.home===pts.away?null:(pts.home>pts.away?'home':'away');const score=Math.max(pts.home,pts.away);return {score,side,reasons,highlight:score>1};}
function calcTotalEdge(game,t){const pts={over:0,under:0};const reasons=[];const kp=totalSignal(game.kenpom.totalEdge,t);if(kp){pts[kp]++;reasons.push('KenPom→'+kp)}const tr=totalSignal(game.trank.totalEdge,t);if(tr){pts[tr]++;reasons.push('T-Rank→'+tr)}const side=pts.over===pts.under?null:(pts.over>pts.under?'over':'under');const score=Math.max(pts.over,pts.under);return {score,side,reasons,highlight:score>1};}
function edgeText(prefix,e){if(!e.reasons.length)return prefix+': no qualifying signals';return prefix+': '+(e.side||'split')+' ('+e.score+') • '+e.reasons.join(', ')}
function renderCards(games){const spreadT=Number(spreadThresholdInput.value)||0;const totalT=Number(totalThresholdInput.value)||0;let highlighted=0;cardsContainer.innerHTML=games.map((g)=>{const side=calcSideEdge(g,spreadT);const total=calcTotalEdge(g,totalT);const hot=side.highlight||total.highlight;if(hot)highlighted++;const time=g.gameTimeEt||'TBD ET';return '<article class="card'+(hot?' highlight':'')+'"><h2 class="line1">'+g.awayTeam+' @ '+g.homeTeam+' | '+time+'</h2>'+
'<div class="row"><span class="label">KenPom predicted score</span><span>'+modelScore(g.kenpom,g.awayTeam,g.homeTeam)+'</span></div>'+
'<div class="row"><span class="label">T-Rank predicted score</span><span>'+modelScore(g.trank,g.awayTeam,g.homeTeam)+'</span></div>'+
'<div class="row"><span class="label">Vegas line</span><span>Spread: '+formatSpread(g.vegas.spread)+' | Total: '+(g.vegas.total??'N/A')+'</span></div>'+
'<div class="row"><span class="label">EDGE</span><div style="width:100%"><div class="edgeBox '+(side.highlight?'hot':'')+'">'+edgeText('Side',side)+'</div><div class="edgeBox '+(total.highlight?'hot':'')+'">'+edgeText('Total',total)+'</div></div></div></article>'}).join('');
if(!games.length){emptyState.textContent=currentReason==='upstream_unavailable'?'No games shown because live schedule fetch is currently unavailable.':'No games scheduled for this date.';emptyState.style.display='block'}else{emptyState.style.display='none'}
statusLine.textContent='Date: '+dateInput.value+' • Source: '+currentSource+' • Games: '+games.length+' • Highlighted: '+highlighted;
}
async function loadPicks(){if(!dateInput.value)dateInput.value=localToday();const r=await fetch('/api/picks?date='+encodeURIComponent(dateInput.value));const data=await r.json();currentGames=data.picks||[];currentSource=data.source||'cache';currentReason=data.reason||'';renderCards(currentGames)}
function renderUpstreamStatus(label,result,target){const code=result.status==null?'no-status':String(result.status);target.textContent=label+': '+code+' '+result.statusText+' ('+result.durationMs+'ms) • '+result.details;}
async function testUpstreams(){if(!dateInput.value)dateInput.value=localToday();apiStatus.textContent='Upstream API status: testing ESPN and The Odds API...';espnStatus.textContent='ESPN: testing...';oddsStatus.textContent='The Odds API: testing...';try{const response=await fetch('/api/test-upstreams?date='+encodeURIComponent(dateInput.value));const data=await response.json();if(!response.ok){apiStatus.textContent='Upstream API status: dashboard test failed with '+response.status+' '+response.statusText;const message=data&&data.error?data.error:'Unable to test upstream APIs.';espnStatus.textContent='ESPN: '+message;oddsStatus.textContent='The Odds API: '+message;return;}apiStatus.textContent='Upstream API status: completed for '+(data.selectedDate||dateInput.value)+'.';renderUpstreamStatus('ESPN',data.espn,espnStatus);renderUpstreamStatus('The Odds API',data.odds,oddsStatus);}catch(err){const message=err&&err.message?err.message:'unknown error';apiStatus.textContent='Upstream API status: request failed - '+message;espnStatus.textContent='ESPN: request failed - '+message;oddsStatus.textContent='The Odds API: request failed - '+message;}}
document.getElementById('today-btn').addEventListener('click',()=>{dateInput.value=localToday();loadPicks()});
document.getElementById('refresh-btn').addEventListener('click',loadPicks);
document.getElementById('test-upstreams-btn').addEventListener('click',testUpstreams);
dateInput.addEventListener('change',loadPicks);spreadThresholdInput.addEventListener('input',()=>renderCards(currentGames));totalThresholdInput.addEventListener('input',()=>renderCards(currentGames));
if(!dateInput.value)dateInput.value=localToday();loadPicks();
</script></body></html>`;

const quickPredict = async (request: Request) => {
  const body = (await request.json()) as Record<string, string>;
  const homeTeamInput = String(body.homeTeam || "").trim();
  const awayTeamInput = String(body.awayTeam || "").trim();
  const neutral = String(body.neutral || "false") === "true";
  if (!homeTeamInput || !awayTeamInput) return new Response(JSON.stringify({ error: "homeTeam and awayTeam are required." }), { status: 400, headers: jsonHeaders });

  const resolvedHomeKenpom = resolveTeamName(homeTeamInput, kenpomLookup);
  const resolvedAwayKenpom = resolveTeamName(awayTeamInput, kenpomLookup);
  const resolvedHomeTrank = resolveTeamName(homeTeamInput, trankLookup);
  const resolvedAwayTrank = resolveTeamName(awayTeamInput, trankLookup);

  const notes: string[] = [];
  let kenpomResult: ReturnType<typeof predictGame> | null = null;
  let trankResult: ReturnType<typeof predictGame> | null = null;
  if (resolvedHomeKenpom && resolvedAwayKenpom) kenpomResult = predictGame(teamModels.kenpom[resolvedHomeKenpom], teamModels.kenpom[resolvedAwayKenpom], neutral);
  else notes.push("KenPom projection unavailable for one or both teams.");
  if (resolvedHomeTrank && resolvedAwayTrank) trankResult = predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], neutral);
  else notes.push("T-Rank projection unavailable for one or both teams.");

  return new Response(JSON.stringify({ homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? homeTeamInput, awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? awayTeamInput, kenpom: kenpomResult, trank: trankResult, notes }), { headers: jsonHeaders });
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/api/picks") {
      const selectedDate = url.searchParams.get("date") || today;
      const result = await getPicksResponse(selectedDate, env);
      return new Response(JSON.stringify(result), { headers: jsonHeaders });
    }
    if (request.method === "GET" && url.pathname === "/api/test-upstreams") {
      const selectedDate = url.searchParams.get("date") || today;
      const [espn, odds] = await Promise.all([testEspnApi(selectedDate), testOddsApi(selectedDate, env)]);
      return new Response(JSON.stringify({ selectedDate, espn, odds }), { headers: jsonHeaders });
    }
    if (request.method === "GET" && url.pathname === "/api/dates") return new Response(JSON.stringify({ dates: Object.keys(payload.dates).sort() }), { headers: jsonHeaders });
    if (request.method === "GET" && url.pathname === "/api/teams") return new Response(JSON.stringify({ teams: teamModels.teams }), { headers: jsonHeaders });
    if (request.method === "POST" && url.pathname === "/api/quick-predict") return quickPredict(request);
    if (request.method === "GET" && url.pathname === "/") return new Response(renderHomePage(), { headers: { "content-type": "text/html; charset=utf-8" } });
    return new Response("Not found", { status: 404 });
  },
};
