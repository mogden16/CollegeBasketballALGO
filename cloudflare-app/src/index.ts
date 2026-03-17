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

type PicksPayload = {
  asOf: string;
  picks: Pick[];
  reason: "ok" | "no_games_scheduled" | "no_cached_data" | "upstream_unavailable";
};

type LineProjection = { spread: number | null; total: number | null };

type PicksResponse = {
  selectedDate: string;
  picks: GamePrediction[];
  source: "cache" | "live";
  reason?: "no_games_scheduled" | "no_cached_data" | "upstream_unavailable";
};

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

type EspnMatchup = { homeTeam: string; awayTeam: string; neutral: boolean };

const payload = gamesData as GamesByDatePayload;
const teamModels = modelsData as TeamModelsPayload;

const jsonHeaders = { "content-type": "application/json; charset=utf-8" };
const todayDate = new Date();
const toIsoDate = (d: Date) => d.toISOString().slice(0, 10);
const today = toIsoDate(todayDate);

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
  if (query.split(" ").length < 2) {
    return null;
  }
  const candidates = [...lookup.keys()].filter((name) => name.startsWith(query) || query.startsWith(name));
  if (candidates.length !== 1) {
    return null;
  }
  return lookup.get(candidates[0]) ?? null;
};

const renderHomePage = () => {
  return `<!doctype html>
  <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>College Basketball Picks</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
        main { max-width: 980px; margin: 0 auto; padding: 1.5rem; }
        h1, h2 { margin-bottom: .25rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: .75rem; }
        .toolbar { display:flex; gap:.6rem; align-items:flex-end; margin-bottom:.8rem; }
        .pick-card { background:#1e293b; border-radius:10px; padding:.8rem; border:1px solid #334155; }
        .muted { color:#94a3b8; margin:.35rem 0 .85rem; font-size:.95rem; }
        .tag { display:inline-block; margin-top:.4rem; border-radius: 99px; padding: .15rem .55rem; font-size:.75rem; font-weight:bold; }
        .high{ background:#166534; } .medium{ background:#854d0e; } .low{ background:#1d4ed8; }
        #empty-state { display:none; margin:.75rem 0 1rem; color:#fbbf24; }
        form { display:grid; grid-template-columns: repeat(2,minmax(120px,1fr)); gap:.65rem; background:#1e293b; padding:1rem; border-radius:10px; border:1px solid #334155; }
        label { display:flex; flex-direction:column; gap:.3rem; font-size:.8rem; }
        input { padding:.45rem; border-radius:8px; border:1px solid #475569; background:#0f172a; color:#e2e8f0; }
        button { grid-column:1/-1; padding:.6rem; border:none; border-radius:8px; cursor:pointer; background:#22c55e; color:#052e16; font-weight:700; }
        #quick-result { margin-top:.8rem; font-weight:bold; }
      </style>
    </head>
    <body>
      <main>
        <h1>Picks of the Day</h1>
        <div class="toolbar">
          <label>
            Date
            <input type="date" id="pick-date" value="${payload.asOf}" />
          </label>
          <button id="load-picks" type="button">Load picks</button>
        </div>
        <p id="status-line" class="muted">As of ${payload.asOf}. Powered by your model output.</p>
        <p id="empty-state"></p>
        <section id="picks-grid" class="grid"></section>

        <h2 style="margin-top:1.25rem;">Quick Predict</h2>
        <form id="quick-form">
          <label>Home Team<input type="text" name="homeTeam" placeholder="Duke" required /></label>
          <label>Away Team<input type="text" name="awayTeam" placeholder="UNC" required /></label>
          <label>Home Off. Rating<input type="number" step="0.1" name="homeOff" required /></label>
          <label>Home Def. Rating<input type="number" step="0.1" name="homeDef" required /></label>
          <label>Away Off. Rating<input type="number" step="0.1" name="awayOff" required /></label>
          <label>Away Def. Rating<input type="number" step="0.1" name="awayDef" required /></label>
          <label>Expected Pace<input type="number" step="0.1" name="pace" value="69" required /></label>
          <button type="submit">Run quick predict</button>
        </form>
        <div id="quick-result"></div>
      </main>
      <script>
        const picksGrid = document.getElementById('picks-grid');
        const statusLine = document.getElementById('status-line');
        const emptyState = document.getElementById('empty-state');
        const pickDate = document.getElementById('pick-date');
        const loadPicksButton = document.getElementById('load-picks');

        const renderCards = (games) => {
          picksGrid.innerHTML = games
            .map((pick) => {
              const vegasSpread = pick.vegasSpread ?? 'N/A';
              return '<article class="pick-card">'
                + '<h3>' + pick.matchup + '</h3>'
                + '<p><strong>Recommended:</strong> ' + pick.recommendedBet + '</p>'
                + '<p><strong>Model spread:</strong> ' + pick.modelSpread + '</p>'
                + '<p><strong>Vegas spread:</strong> ' + vegasSpread + '</p>'
                + '<p><strong>Edge:</strong> ' + pick.edge + '</p>'
                + '<span class="tag ' + pick.confidence.toLowerCase() + '">' + pick.confidence + '</span>'
                + '</article>';
            })
            .join('');
        };

        const reasonMessages = {
          no_games_scheduled: 'No games are scheduled for the selected date.',
          no_cached_data: 'No cached picks are available yet for that date.',
          upstream_unavailable: 'Live picks service is temporarily unavailable. Please try again shortly.'
        };

        const shortReasonText = {
          no_games_scheduled: 'no slate',
          no_cached_data: 'no cache',
          upstream_unavailable: 'service unavailable'
        };

        const loadPicks = async () => {
          const date = pickDate.value;
          try {
            const response = await fetch('/api/picks?date=' + encodeURIComponent(date));
            const data = await response.json();
            const games = Array.isArray(data.picks) ? data.picks : [];
            const reason = data.reason;

            renderCards(games);
            const reasonSuffix = games.length === 0 && shortReasonText[reason] ? ' (' + shortReasonText[reason] + ')' : '';
            statusLine.textContent = 'As of ' + data.asOf + '. Powered by your model output.' + reasonSuffix;

            if (games.length === 0) {
              emptyState.style.display = 'block';
              emptyState.textContent = reasonMessages[reason] || 'No picks available for the selected date.';
            } else {
              emptyState.style.display = 'none';
              emptyState.textContent = '';
            }
          } catch (error) {
            renderCards([]);
            statusLine.textContent = 'As of ' + date + '. Powered by your model output. (service unavailable)';
            emptyState.style.display = 'block';
            emptyState.textContent = reasonMessages.upstream_unavailable;
          }
        };

        loadPicksButton.addEventListener('click', loadPicks);
        pickDate.addEventListener('change', loadPicks);
        loadPicks();

        const form = document.getElementById('quick-form');
        const result = document.getElementById('quick-result');
        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          const formData = new FormData(form);
          const body = Object.fromEntries(formData.entries());

          const response = await fetch('/api/quick-predict', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(body)
          });

          const data = await response.json();
          const homeTeam = String(body.homeTeam || 'Home');
          const awayTeam = String(body.awayTeam || 'Away');
          const summary = [
            awayTeam,
            ' vs ',
            homeTeam,
            ' - Projected team totals: ',
            awayTeam,
            ' ',
            String(data.awayScore),
            ', ',
            homeTeam,
            ' ',
            String(data.homeScore),
            '. Overall total: ',
            String(data.projectedTotal),
          ].join('');
          result.textContent = summary;
        });
      }
    }

    return matchups;
  } catch {
    return null;
  }
};

const buildLiveGamesForDate = async (selectedDate: string): Promise<PicksResponse> => {
  const schedule = await fetchEspnSchedule(selectedDate);
  if (schedule === null) {
    return {
      selectedDate,
      picks: [],
      source: "live",
      reason: "upstream_unavailable",
    };
  }

  const picks: GamePrediction[] = [];
  for (const game of schedule) {
    const resolvedHomeKenpom = resolveTeamName(game.homeTeam, kenpomLookup);
    const resolvedAwayKenpom = resolveTeamName(game.awayTeam, kenpomLookup);
    const resolvedHomeTrank = resolveTeamName(game.homeTeam, trankLookup);
    const resolvedAwayTrank = resolveTeamName(game.awayTeam, trankLookup);

    const kenpomProjection: ModelProjection = {
      homeScore: null,
      awayScore: null,
      total: null,
      spread: null,
      spreadEdge: null,
      totalEdge: null,
    };

    const trankProjection: ModelProjection = {
      homeScore: null,
      awayScore: null,
      total: null,
      spread: null,
      spreadEdge: null,
      totalEdge: null,
    };

    if (resolvedHomeKenpom && resolvedAwayKenpom) {
      const calc = predictGame(teamModels.kenpom[resolvedHomeKenpom], teamModels.kenpom[resolvedAwayKenpom], game.neutral);
      kenpomProjection.homeScore = calc.homeScore;
      kenpomProjection.awayScore = calc.awayScore;
      kenpomProjection.total = calc.total;
      kenpomProjection.spread = calc.spread;
    }

    if (resolvedHomeTrank && resolvedAwayTrank) {
      const calc = predictGame(teamModels.trank[resolvedHomeTrank], teamModels.trank[resolvedAwayTrank], game.neutral);
      trankProjection.homeScore = calc.homeScore;
      trankProjection.awayScore = calc.awayScore;
      trankProjection.total = calc.total;
      trankProjection.spread = calc.spread;
    }

    picks.push({
      homeTeam: resolvedHomeKenpom ?? resolvedHomeTrank ?? game.homeTeam,
      awayTeam: resolvedAwayKenpom ?? resolvedAwayTrank ?? game.awayTeam,
      neutral: game.neutral,
      kenpom: kenpomProjection,
      trank: trankProjection,
      vegas: { spread: null, total: null },
      isEdge: false,
      confidence: null,
    });
  }

  return {
    selectedDate,
    picks,
    source: "live",
    reason: picks.length ? undefined : "no_games_scheduled",
  };
};

const getPicksResponse = async (selectedDate: string): Promise<PicksResponse> => {
  const cached = getGamesForDate(selectedDate);
  if (cached.length > 0) {
    return {
      selectedDate,
      picks: cached,
      source: "cache",
    };
  }

  const liveResult = await buildLiveGamesForDate(selectedDate);
  if (liveResult.picks.length > 0 || liveResult.reason === "upstream_unavailable") {
    return liveResult;
  }

  return {
    selectedDate,
    picks: [],
    source: "cache",
    reason: "no_cached_data",
  };
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
      body { margin: 0; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#020617; color:#e2e8f0; }
      main { max-width: 1260px; margin: 0 auto; padding: 1.25rem; }
      .hero { display:flex; flex-wrap: wrap; gap: 1rem; justify-content:space-between; align-items:flex-end; margin-bottom: 1.25rem; }
      h1 { margin: 0; font-size: clamp(1.5rem, 3.8vw, 2.2rem); letter-spacing:.01em; }
      .subtitle { margin:.4rem 0 0; color:#94a3b8; font-size:.95rem; }
      .layout { display:grid; gap:1rem; grid-template-columns: minmax(0, 1fr) 280px; align-items:start; }
      .content { min-width: 0; }
      .controls { position: sticky; top: 1rem; background:#0b1220; border:1px solid #1f314d; border-radius:14px; padding:1rem; }
      .controls h3 { margin:.1rem 0 .8rem; font-size:.95rem; }
      .control-group { margin-bottom:.7rem; }
      .control-group label { display:block; color:#cbd5e1; font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; margin-bottom:.25rem; }
      input, select, button { width:100%; padding:.6rem .7rem; border-radius:10px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; }
      input:focus, select:focus { outline: none; border-color:#38bdf8; }
      .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1rem; }
      .card { background:linear-gradient(180deg, #0f172a, #0b1220); border:1px solid #1f314d; border-radius:14px; box-shadow: 0 12px 28px rgba(2,6,23,.4); padding:1rem; }
      .card.highlight { border-color:#f59e0b; box-shadow: 0 0 0 1px rgba(245,158,11,.45), 0 12px 28px rgba(2,6,23,.4); }
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
      .small-note { color:#94a3b8; font-size:.82rem; margin-top: .5rem; }
      @media (max-width: 980px) {
        .layout { grid-template-columns: 1fr; }
        .controls { position: static; }
      }
    </style>
  </head>
  <body>
    <main>
      <header class="hero">
        <div>
          <h1>Picks of the Day</h1>
          <p class="subtitle">All available games for the selected date. Highlight = spread and total discrepancies exceed your thresholds.</p>
        </div>
      </header>

      <div class="layout">
        <section class="content">
          <section id="cards" class="grid"></section>
          <p id="empty-state">No games found for this date. Try another date from the controls.</p>

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
        </section>

        <aside class="controls">
          <h3>Filters</h3>
          <div class="control-group">
            <label for="pick-date">Selected Date</label>
            <input id="pick-date" type="date" value="${today}" min="${toIsoDate(minDate)}" max="${toIsoDate(maxDate)}" />
          </div>
          <div class="control-group">
            <label for="spread-threshold">Spread discrepancy X (pts)</label>
            <input id="spread-threshold" type="number" value="3" step="0.5" min="0" />
          </div>
          <div class="control-group">
            <label for="total-threshold">Total discrepancy X (pts)</label>
            <input id="total-threshold" type="number" value="5" step="0.5" min="0" />
          </div>
          <p class="small-note" id="status-line">Date: ${today}</p>
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
      let currentGames = [];
      let currentSource = 'cache';
      let currentReason = '';

      const asLine = (label, value) => '<span>' + label + ': ' + value + '</span>';
      const formatSpread = (spread) => {
        if (spread == null) return 'N/A';
        return spread < 0 ? 'Home ' + spread : 'Away +' + spread;
      };

      const modelHasDiscrepancy = (model, spreadThreshold, totalThreshold) =>
        model &&
        model.spreadEdge != null &&
        model.totalEdge != null &&
        Math.abs(model.spreadEdge) >= spreadThreshold &&
        Math.abs(model.totalEdge) >= totalThreshold;

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

      function renderEmptyState() {
        if (!currentGames.length) {
          if (currentReason === 'upstream_unavailable') {
            emptyState.textContent = 'No games shown because live schedule fetch is currently unavailable.';
          } else if (currentReason === 'no_cached_data') {
            emptyState.textContent = 'No cached data for this date and no live games were returned.';
          } else {
            emptyState.textContent = 'No games scheduled for this date.';
          }
          emptyState.style.display = 'block';
          return;
        }
        emptyState.style.display = 'none';
      }

      function renderCards(games) {
        const spreadThreshold = Number(spreadThresholdInput.value) || 0;
        const totalThreshold = Number(totalThresholdInput.value) || 0;
        let highlighted = 0;

        cardsContainer.innerHTML = games.map((game) => {
          const shouldHighlight =
            modelHasDiscrepancy(game.kenpom, spreadThreshold, totalThreshold) ||
            modelHasDiscrepancy(game.trank, spreadThreshold, totalThreshold);
          if (shouldHighlight) highlighted += 1;

          return '<article class="card' + (shouldHighlight ? ' highlight' : '') + '">' +
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
          '</article>';
        }).join('');

        const reasonText = currentReason ? ' • Reason: ' + currentReason : '';
        statusLine.textContent =
          'Date: ' + dateInput.value +
          ' • Source: ' + currentSource +
          ' • Games: ' + games.length +
          ' • Highlighted: ' + highlighted +
          ' (Spread ≥ ' + spreadThreshold + ', Total ≥ ' + totalThreshold + ')' +
          reasonText;

        renderEmptyState();
      }

      async function loadTeams() {
        const response = await fetch('/api/teams');
        const data = await response.json();
        teamList.innerHTML = data.teams.map((team) => '<option value="' + team + '"></option>').join('');
      }

      async function loadPicks() {
        const selectedDate = dateInput.value;
        const response = await fetch('/api/picks?date=' + encodeURIComponent(selectedDate));
        const data = await response.json();
        currentGames = data.picks;
        currentSource = data.source || 'cache';
        currentReason = data.reason || '';
        renderCards(currentGames);
      }

      dateInput.addEventListener('change', loadPicks);
      dateInput.addEventListener('input', loadPicks);
      spreadThresholdInput.addEventListener('input', () => renderCards(currentGames));
      totalThresholdInput.addEventListener('input', () => renderCards(currentGames));

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
      const requestedDate = url.searchParams.get("date");
      if (requestedDate && requestedDate !== payload.asOf) {
        return new Response(
          JSON.stringify({ asOf: requestedDate, picks: [], reason: "no_cached_data" }),
          { headers: jsonHeaders },
        );
      }

      const reason = payload.picks.length === 0 ? "no_games_scheduled" : "ok";
      return new Response(JSON.stringify({ ...payload, reason }), { headers: jsonHeaders });
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
