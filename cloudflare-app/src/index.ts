import gamesData from "../data/games-by-date.json";
import modelsData from "../data/team-models.json";

type GamePrediction = {
  homeTeam: string;
  awayTeam: string;
  neutral: boolean;
  kenpom: ModelProjection;
  trank: ModelProjection;
  vegas: LineProjection;
  isEdge: boolean;
  confidence: string | null;
};

type ModelProjection = {
  homeScore: number | null;
  awayScore: number | null;
  total: number | null;
  spread: number | null;
  spreadEdge: number | null;
  totalEdge: number | null;
};

type LineProjection = { spread: number | null; total: number | null };

type GamesByDatePayload = {
  dates: Record<string, GamePrediction[]>;
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

const payload = gamesData as GamesByDatePayload;
const teamModels = modelsData as TeamModelsPayload;

const jsonHeaders = { "content-type": "application/json; charset=utf-8" };
const today = new Date().toISOString().slice(0, 10);

const TEAM_ALIASES: Record<string, string> = {
  "uconn": "Connecticut",
  "u conn": "Connecticut",
  "unc": "North Carolina",
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
  for (const team of teams) {
    lookup.set(normalizeTeamName(team), team);
  }
  return lookup;
};

const kenpomLookup = buildNormalizedLookup(Object.keys(teamModels.kenpom));
const trankLookup = buildNormalizedLookup(Object.keys(teamModels.trank));

const deterministicFallback = (query: string, lookup: Map<string, string>): string | null => {
  const tokens = query.split(" ");
  if (tokens.length < 2) {
    return null;
  }
  const candidates = [...lookup.keys()].filter((name) => name.startsWith(query) || query.startsWith(name));
  if (candidates.length !== 1) {
    return null;
  }
  return lookup.get(candidates[0]) ?? null;
};

const resolveTeamName = (teamName: string, lookup: Map<string, string>): string | null => {
  const normalized = normalizeTeamName(teamName);
  const exact = lookup.get(normalized);
  if (exact) {
    return exact;
  }

  const alias = TEAM_ALIASES[normalized];
  if (alias) {
    const aliasExact = lookup.get(normalizeTeamName(alias));
    if (aliasExact) {
      return aliasExact;
    }
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
  return {
    homeScore,
    awayScore,
    total: Number((homeScore + awayScore).toFixed(1)),
    spread,
  };
};

const getGamesForDate = (selectedDate: string): GamePrediction[] => payload.dates[selectedDate] ?? [];

const renderHomePage = () => `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>College Basketball Dashboard</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body { margin: 0; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#020617; color:#e2e8f0; }
      main { max-width: 1200px; margin: 0 auto; padding: 1.25rem; }
      .hero { display:flex; flex-wrap: wrap; gap: 1rem; justify-content:space-between; align-items:flex-end; margin-bottom: 1.25rem; }
      h1 { margin: 0; font-size: clamp(1.5rem, 3.8vw, 2.2rem); letter-spacing:.01em; }
      .subtitle { margin:.4rem 0 0; color:#94a3b8; font-size:.95rem; }
      .date-wrap { display:flex; flex-direction:column; gap:.35rem; min-width:220px; }
      .date-wrap label { color:#cbd5e1; font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; }
      input, select, button { width:100%; padding:.6rem .7rem; border-radius:10px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; }
      input:focus, select:focus { outline: none; border-color:#38bdf8; }
      .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1rem; }
      .card { background:linear-gradient(180deg, #0f172a, #0b1220); border:1px solid #1f314d; border-radius:14px; box-shadow: 0 12px 28px rgba(2,6,23,.4); padding:1rem; }
      .matchup { margin:0 0 .85rem; font-size:1rem; font-weight:700; }
      .section { border-top:1px solid #1e293b; padding-top:.6rem; margin-top:.6rem; }
      .section h3 { margin:0 0 .45rem; font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:#93c5fd; }
      .scoreline { font-size:1rem; font-weight:700; margin-bottom:.35rem; }
      .meta { display:flex; gap:.5rem; flex-wrap:wrap; color:#cbd5e1; font-size:.88rem; }
      .edge { margin-top:.5rem; border-radius:10px; padding:.55rem .65rem; border:1px solid #14532d; background: rgba(22, 101, 52, .16); color:#dcfce7; font-size:.9rem; }
      .edge.flat { border-color:#374151; background:rgba(30,41,59,.45); color:#d1d5db; }
      .pill { display:inline-block; margin-left:.5rem; border-radius:999px; padding:.12rem .48rem; font-size:.72rem; background:#1e293b; color:#cbd5e1; }
      .panel { margin-top: 1.3rem; background:#0b1220; border:1px solid #1f314d; border-radius:14px; padding:1rem; }
      .quick-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:.7rem; }
      .actions { display:flex; align-items:flex-end; }
      button { background:#22c55e; border-color:#22c55e; color:#052e16; font-weight:700; cursor:pointer; }
      #empty-state { display:none; margin-top:1rem; color:#94a3b8; }
      #quick-result { margin-top:.85rem; }
      .result-card { border:1px solid #334155; border-radius:12px; padding:.8rem; background:#0f172a; }
    </style>
  </head>
  <body>
    <main>
      <header class="hero">
        <div>
          <h1>Picks of the Day</h1>
          <p class="subtitle">Model dashboard for KenPom, T-Rank, and market lines.</p>
        </div>
        <div class="date-wrap">
          <label for="pick-date">Selected Date</label>
          <input id="pick-date" type="date" value="${today}" />
        </div>
      </header>

      <section id="cards" class="grid"></section>
      <p id="empty-state">No games found for this date. Try another date from the calendar.</p>

      <section class="panel">
        <h2 style="margin:0 0 .75rem;">Quick Predict</h2>
        <form id="quick-form">
          <div class="quick-grid">
            <label>Home Team<input list="team-list" type="text" name="homeTeam" placeholder="Duke" required /></label>
            <label>Away Team<input list="team-list" type="text" name="awayTeam" placeholder="North Carolina" required /></label>
            <label>Neutral Court?
              <select name="neutral"><option value="false">No</option><option value="true">Yes</option></select>
            </label>
            <div class="actions"><button type="submit">Run Quick Predict</button></div>
          </div>
          <datalist id="team-list"></datalist>
        </form>
        <div id="quick-result"></div>
      </section>
    </main>
    <script>
      const cardsContainer = document.getElementById('cards');
      const dateInput = document.getElementById('pick-date');
      const emptyState = document.getElementById('empty-state');
      const teamList = document.getElementById('team-list');
      const quickForm = document.getElementById('quick-form');
      const quickResult = document.getElementById('quick-result');

      const asLine = (label, value) => value == null ? '' : '<span>' + label + ': ' + value + '</span>';
      const formatSpread = (spread) => {
        if (spread == null) return 'N/A';
        return spread < 0 ? 'Home ' + spread : 'Away +' + spread;
      };

      const renderEdge = (game) => {
        const parts = [];
        if (game.kenpom.spreadEdge != null) parts.push('KenPom spread edge: ' + (game.kenpom.spreadEdge > 0 ? 'Home' : 'Away') + ' +' + Math.abs(game.kenpom.spreadEdge).toFixed(1));
        if (game.trank.spreadEdge != null) parts.push('T-Rank spread edge: ' + (game.trank.spreadEdge > 0 ? 'Home' : 'Away') + ' +' + Math.abs(game.trank.spreadEdge).toFixed(1));
        if (game.kenpom.totalEdge != null) parts.push('KenPom total: ' + (game.kenpom.totalEdge > 0 ? 'Over' : 'Under') + ' ' + Math.abs(game.kenpom.totalEdge).toFixed(1));
        if (game.trank.totalEdge != null) parts.push('T-Rank total: ' + (game.trank.totalEdge > 0 ? 'Over' : 'Under') + ' ' + Math.abs(game.trank.totalEdge).toFixed(1));
        if (!parts.length) return '<div class="edge flat">No actionable edge from available model + line data.</div>';
        return '<div class="edge">' + parts.join('<br/>') + '</div>';
      };

      const modelBlock = (title, model, awayTeam, homeTeam) =>
        '<div class="section">' +
          '<h3>' + title + '</h3>' +
          '<div class="scoreline">' + awayTeam + ' (' + (model.awayScore ?? 'N/A') + ') vs ' + homeTeam + ' (' + (model.homeScore ?? 'N/A') + ')</div>' +
          '<div class="meta">' +
            asLine('Spread', formatSpread(model.spread)) +
            asLine('Total', model.total ?? 'N/A') +
          '</div>' +
        '</div>';

      async function loadTeams() {
        const response = await fetch('/api/teams');
        const data = await response.json();
        teamList.innerHTML = data.teams.map(team => '<option value="' + team + '"></option>').join('');
      }

      async function loadPicks() {
        const selectedDate = dateInput.value;
        const response = await fetch('/api/picks?date=' + selectedDate);
        const data = await response.json();

        cardsContainer.innerHTML = data.picks.map((game) =>
          '<article class="card">' +
            '<h2 class="matchup">' + game.awayTeam + ' @ ' + game.homeTeam + ' ' + (game.confidence ? '<span class="pill">' + game.confidence + '</span>' : '') + '</h2>' +
            modelBlock('KenPom', game.kenpom, game.awayTeam, game.homeTeam) +
            modelBlock('T-Rank', game.trank, game.awayTeam, game.homeTeam) +
            '<div class="section">' +
              '<h3>Vegas Line</h3>' +
              '<div class="meta">' +
                asLine('Spread', formatSpread(game.vegas.spread)) +
                asLine('Total', game.vegas.total ?? 'N/A') +
              '</div>' +
            '</div>' +
            '<div class="section">' +
              '<h3>Edge Summary</h3>' +
              renderEdge(game) +
            '</div>' +
          '</article>'
        ).join('');

        emptyState.style.display = data.picks.length ? 'none' : 'block';
      }

      dateInput.addEventListener('change', loadPicks);

      quickForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const body = Object.fromEntries(new FormData(quickForm).entries());
        const response = await fetch('/api/quick-predict', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await response.json();

        const rows = [];
        if (data.kenpom) rows.push('<p><strong>KenPom:</strong> Spread ' + data.kenpom.spread + ' | Total ' + data.kenpom.total + '</p>');
        if (data.trank) rows.push('<p><strong>T-Rank:</strong> Spread ' + data.trank.spread + ' | Total ' + data.trank.total + '</p>');
        if (data.notes && data.notes.length) rows.push('<p style="color:#fbbf24">' + data.notes.join(' ') + '</p>');

        quickResult.innerHTML =
          '<div class="result-card">' +
            '<h3 style="margin:0 0 .45rem;">' + data.awayTeam + ' @ ' + data.homeTeam + '</h3>' +
            (rows.join('') || '<p>No model output available for that matchup.</p>') +
          '</div>';
      });

      loadTeams();
      loadPicks();
    </script>
  </body>
</html>`;

const quickPredict = async (request: Request) => {
  const body = (await request.json()) as Record<string, string>;
  const homeTeamInput = String(body.homeTeam || "").trim();
  const awayTeamInput = String(body.awayTeam || "").trim();
  const neutral = String(body.neutral || "false") === "true";

  if (!homeTeamInput || !awayTeamInput) {
    return new Response(JSON.stringify({ error: "homeTeam and awayTeam are required." }), {
      status: 400,
      headers: jsonHeaders,
    });
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
  } else {
    notes.push("KenPom projection unavailable for one or both teams.");
  }

  if (resolvedHomeTrank && resolvedAwayTrank) {
    trankResult = predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], neutral);
  } else {
    notes.push("T-Rank projection unavailable for one or both teams.");
  }

  return new Response(
    JSON.stringify({
      homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? homeTeamInput,
      awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? awayTeamInput,
      kenpom: kenpomResult,
      trank: trankResult,
      notes,
    }),
    { headers: jsonHeaders },
  );
};

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/api/picks") {
      const selectedDate = url.searchParams.get("date") || today;
      const picks = getGamesForDate(selectedDate);
      return new Response(JSON.stringify({ selectedDate, picks }), { headers: jsonHeaders });
    }

    if (request.method === "GET" && url.pathname === "/api/dates") {
      return new Response(JSON.stringify({ dates: Object.keys(payload.dates).sort() }), { headers: jsonHeaders });
    }

    if (request.method === "GET" && url.pathname === "/api/teams") {
      return new Response(JSON.stringify({ teams: teamModels.teams }), { headers: jsonHeaders });
    }

    if (request.method === "POST" && url.pathname === "/api/quick-predict") {
      return quickPredict(request);
    }

    if (request.method === "GET" && url.pathname === "/") {
      return new Response(renderHomePage(), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
