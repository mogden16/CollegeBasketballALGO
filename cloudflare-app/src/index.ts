import modelsData from "../data/team-models.json";
import { normalizeTeamName } from "./teamName";
import { predictGame, buildConsensus, type TeamRatings, type MatchupResult } from "./matchup";

// ── Types ────────────────────────────────────────────────────────────────────
type TeamModelsPayload = {
  kenpom: Record<string, TeamRatings>;
  trank  : Record<string, TeamRatings>;
  teams  : string[];
};

type QuickMatchupBody = {
  teamA       : string;
  teamB       : string;
  neutral     : boolean;
  useDampening: boolean;
};

// ── Constants ────────────────────────────────────────────────────────────────
const teamModels  = modelsData as TeamModelsPayload;
const jsonHeaders = { "content-type": "application/json; charset=utf-8" } as const;

const TEAM_ALIASES: Record<string, string> = {
  uconn        : "Connecticut",
  "u conn"     : "Connecticut",
  unc          : "North Carolina",
  "st johns"   : "St. John's",
  "st john"    : "St. John's",
  "saint johns": "St. John's",
  "saint john" : "St. John's",
  "iowa st"    : "Iowa St.",
  "michigan st": "Michigan St.",
  "texas am"   : "Texas A&M",
};

// ── Team-name resolution ─────────────────────────────────────────────────────
const buildNormalizedLookup = (teams: string[]): Map<string, string> => {
  const m = new Map<string, string>();
  for (const t of teams) m.set(normalizeTeamName(t), t);
  return m;
};

const kenpomLookup = buildNormalizedLookup(Object.keys(teamModels.kenpom));
const trankLookup  = buildNormalizedLookup(Object.keys(teamModels.trank));

const deterministicFallback = (query: string, lookup: Map<string, string>): string | null => {
  if (query.split(" ").length < 2) return null;
  const candidates = [...lookup.keys()].filter(n => n.startsWith(query) || query.startsWith(n));
  if (candidates.length !== 1) return null;
  return lookup.get(candidates[0]) ?? null;
};

const resolveTeamName = (input: string, lookup: Map<string, string>): string | null => {
  const norm  = normalizeTeamName(input);
  const exact = lookup.get(norm);
  if (exact) return exact;
  const alias = TEAM_ALIASES[norm];
  if (alias) {
    const aliasExact = lookup.get(normalizeTeamName(alias));
    if (aliasExact) return aliasExact;
  }
  return deterministicFallback(norm, lookup);
};

// ── Matchup API handler ───────────────────────────────────────────────────────
const handleMatchup = async (request: Request): Promise<Response> => {
  let body: Partial<QuickMatchupBody>;
  try {
    body = (await request.json()) as Partial<QuickMatchupBody>;
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body." }), { status: 400, headers: jsonHeaders });
  }

  const teamAInput    = String(body.teamA       ?? "").trim();
  const teamBInput    = String(body.teamB       ?? "").trim();
  const neutral       = body.neutral       === true;
  const useDampening  = body.useDampening  !== false; // default true

  if (!teamAInput || !teamBInput) {
    return new Response(JSON.stringify({ error: "teamA and teamB are required." }), { status: 400, headers: jsonHeaders });
  }

  const resolvedAKp = resolveTeamName(teamAInput, kenpomLookup);
  const resolvedBKp = resolveTeamName(teamBInput, kenpomLookup);
  const resolvedATr = resolveTeamName(teamAInput, trankLookup);
  const resolvedBTr = resolveTeamName(teamBInput, trankLookup);

  const notes: string[] = [];
  let kenpomProj  = null;
  let trankProj   = null;

  if (resolvedAKp && resolvedBKp) {
    kenpomProj = predictGame(teamModels.kenpom[resolvedAKp], teamModels.kenpom[resolvedBKp], neutral, useDampening);
  } else {
    notes.push(`KenPom data unavailable for: ${!resolvedAKp ? teamAInput : teamBInput}.`);
  }

  if (resolvedATr && resolvedBTr) {
    trankProj = predictGame(teamModels.trank[resolvedATr], teamModels.trank[resolvedBTr], neutral, useDampening);
  } else {
    notes.push(`T-Rank data unavailable for: ${!resolvedATr ? teamAInput : teamBInput}.`);
  }

  const consensusProj = kenpomProj && trankProj
    ? buildConsensus(kenpomProj, trankProj)
    : kenpomProj ?? trankProj ?? null;

  const result: MatchupResult = {
    teamA       : resolvedAKp ?? resolvedATr ?? teamAInput,
    teamB       : resolvedBKp ?? resolvedBTr ?? teamBInput,
    neutral,
    useDampening,
    kenpom      : kenpomProj,
    trank       : trankProj,
    consensus   : consensusProj,
    notes,
  };

  return new Response(JSON.stringify(result), { headers: jsonHeaders });
};

// ── Home page ─────────────────────────────────────────────────────────────────
const renderHomePage = (teams: string[]): string => {
  const teamsJson = JSON.stringify(teams);
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>CBB Matchup Analyzer</title>
<style>
:root{
  --bg:#060d1c;--card:#0c1628;--card2:#101e34;
  --border:#1a2e4a;--border2:#243d5e;
  --text:#dce8f5;--muted:#7a93b0;--dim:#3d5470;
  --blue:#4a90e2;--blue-d:rgba(74,144,226,.13);
  --red:#f07070;--red-d:rgba(240,112,112,.13);
  --green:#38d9a9;--amber:#fbbf24;--amber-d:rgba(251,191,36,.13);
  color-scheme:dark;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:3rem}
a{color:var(--blue)}
h1{font-size:1.7rem;font-weight:800;letter-spacing:-.03em}
h2{font-size:1.1rem;font-weight:700;letter-spacing:-.01em}
h3{font-size:.82rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}

/* ── Layout ───────────────────────────────── */
.app{max-width:1080px;margin:0 auto;padding:1.5rem 1rem}
.header{display:flex;flex-direction:column;gap:.25rem;margin-bottom:1.75rem}
.header .sub{color:var(--muted);font-size:.88rem}
.section+.section{margin-top:1rem}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:700px){.two-col{grid-template-columns:1fr}}

/* ── Cards ───────────────────────────────── */
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.25rem}
.card-title{margin-bottom:1rem}

/* ── Builder ─────────────────────────────── */
.builder-teams{display:flex;align-items:flex-end;gap:.75rem;flex-wrap:wrap}
.team-field{flex:1;min-width:160px;display:flex;flex-direction:column;gap:.35rem}
.team-field label{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.team-field input{width:100%;padding:.65rem .8rem;border-radius:10px;border:1px solid var(--border2);background:#0a1525;color:var(--text);font-size:.95rem;outline:none;transition:border-color .15s}
.team-field input:focus{border-color:var(--blue)}
.at-sep{font-size:1.5rem;font-weight:700;color:var(--dim);align-self:flex-end;padding-bottom:.7rem}
/* ── Autocomplete ────────────────────────── */
.ac-wrap{position:relative}
.ac-list{position:absolute;top:calc(100% + 4px);left:0;right:0;z-index:200;background:#0d1e38;border:1px solid var(--border2);border-radius:10px;max-height:220px;overflow-y:auto;display:none;list-style:none;padding:.3rem 0;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.ac-list.open{display:block}
.ac-list li{padding:.48rem .8rem;cursor:pointer;font-size:.9rem;color:var(--text);border-bottom:1px solid rgba(255,255,255,.04)}
.ac-list li:last-child{border-bottom:none}
.ac-list li:hover,.ac-list li.ac-hi{background:var(--blue-d);color:var(--blue)}
.builder-options{display:flex;align-items:center;flex-wrap:wrap;gap:1rem;margin-top:.9rem}
.toggle-group{display:flex;flex-direction:column;gap:.35rem}
.toggle-group label{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.toggle-btn{display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border2)}
.toggle-btn button{padding:.42rem .85rem;border:none;background:#0a1525;color:var(--muted);font-size:.82rem;font-weight:600;cursor:pointer;transition:all .15s}
.toggle-btn button.active{background:var(--blue);color:#fff}
.builder-actions{display:flex;gap:.6rem;margin-top:1.1rem}
.btn{padding:.6rem 1.4rem;border-radius:10px;border:none;font-size:.9rem;font-weight:700;cursor:pointer;transition:all .15s;letter-spacing:.01em}
.btn-primary{background:var(--blue);color:#fff}
.btn-primary:hover{background:#5a9fe8}
.btn-primary:disabled{opacity:.45;cursor:not-allowed}
.btn-secondary{background:var(--card2);color:var(--muted);border:1px solid var(--border2)}
.btn-secondary:hover{color:var(--text)}

/* ── Summary card ────────────────────────── */
.summary-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.5rem}
.matchup-label{font-size:1.05rem;font-weight:700;margin-bottom:1.25rem;color:var(--text)}
.win-prob-bar-wrap{margin-bottom:1.25rem}
.win-prob-teams{display:flex;justify-content:space-between;margin-bottom:.4rem}
.win-prob-team{display:flex;flex-direction:column;gap:.15rem}
.win-prob-team .team-name{font-size:.8rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.win-prob-team .pct{font-size:2rem;font-weight:800;letter-spacing:-.04em}
.win-prob-team.a .pct{color:var(--blue)}
.win-prob-team.b .pct{color:var(--red);text-align:right}
.win-prob-bar{height:10px;border-radius:6px;overflow:hidden;background:var(--card2);display:flex}
.win-prob-bar .seg-a{background:var(--blue);transition:width .4s cubic-bezier(.4,0,.2,1)}
.win-prob-bar .seg-b{background:var(--red);flex:1}
.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;margin-top:.9rem}
@media(max-width:600px){.summary-grid{grid-template-columns:repeat(2,1fr)}}
.stat-box{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:.7rem .9rem}
.stat-box .s-label{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.3rem}
.stat-box .s-value{font-size:1.15rem;font-weight:700}
.confidence-row{display:flex;align-items:center;gap:.75rem;margin-top:1rem;flex-wrap:wrap}
.badge{display:inline-block;padding:.28rem .75rem;border-radius:20px;font-size:.78rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.badge.toss-up{background:rgba(123,146,175,.15);color:#7b92af;border:1px solid rgba(123,146,175,.3)}
.badge.lean{background:var(--amber-d);color:var(--amber);border:1px solid rgba(251,191,36,.3)}
.badge.strong-lean{background:rgba(251,140,36,.15);color:#fb8c24;border:1px solid rgba(251,140,36,.3)}
.badge.model-lean-a{background:var(--blue-d);color:var(--blue);border:1px solid rgba(74,144,226,.3)}
.badge.model-lean-b{background:var(--red-d);color:var(--red);border:1px solid rgba(240,112,112,.3)}
.badge.pass{background:rgba(100,116,139,.12);color:#8fa0b4;border:1px solid rgba(100,116,139,.25)}

/* ── Projections table ───────────────────── */
.proj-table{width:100%;border-collapse:collapse;font-size:.88rem}
.proj-table th{text-align:left;padding:.45rem .7rem;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);border-bottom:1px solid var(--border)}
.proj-table td{padding:.6rem .7rem;border-bottom:1px solid var(--border)}
.proj-table tr:last-child td{border-bottom:none}
.proj-table .src-label{font-weight:700;color:var(--text)}
.proj-table .score{font-weight:600;font-size:.95rem}
.proj-table .winner-badge{background:var(--blue-d);color:var(--blue);border:1px solid rgba(74,144,226,.25);border-radius:6px;padding:.15rem .45rem;font-size:.72rem;font-weight:700}
.proj-table .winner-badge.b{background:var(--red-d);color:var(--red);border-color:rgba(240,112,112,.25)}
.proj-table tr.consensus td{background:rgba(255,255,255,.025)}

/* ── Sliders ─────────────────────────────── */
.slider-group{display:flex;flex-direction:column;gap:.85rem}
.slider-item{display:flex;flex-direction:column;gap:.4rem}
.slider-row{display:flex;align-items:center;gap:.65rem}
.slider-item label{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.slider-item input[type=range]{flex:1;-webkit-appearance:none;height:5px;border-radius:4px;background:var(--border2);outline:none;cursor:pointer}
.slider-item input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--blue);cursor:pointer}
.slider-item input[type=range]:disabled{opacity:.35;cursor:not-allowed}
.slider-val{min-width:38px;text-align:right;font-size:.88rem;font-weight:700;color:var(--blue)}
.slider-item .adj-hint{font-size:.7rem;color:var(--dim)}

/* ── Histogram ───────────────────────────── */
.histogram-wrap{overflow:hidden;border-radius:8px;background:var(--card2);padding:.75rem .5rem .25rem}
.histogram-legend{display:flex;gap:1rem;margin-top:.6rem;justify-content:center}
.hist-leg-item{display:flex;align-items:center;gap:.35rem;font-size:.72rem;color:var(--muted)}
.hist-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}

/* ── Spread evaluator ────────────────────── */
.spread-row{display:flex;gap:.75rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:1rem}
.spread-field{display:flex;flex-direction:column;gap:.35rem}
.spread-field label{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.spread-field select,.spread-field input{padding:.6rem .8rem;border-radius:10px;border:1px solid var(--border2);background:#0a1525;color:var(--text);font-size:.9rem;outline:none}
.spread-field select{min-width:130px}
.spread-field input{width:110px}
.spread-result{background:var(--card2);border:1px solid var(--border);border-radius:12px;padding:1rem 1.25rem}
.spread-result-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem}
@media(max-width:500px){.spread-result-grid{grid-template-columns:1fr 1fr}}
.sr-item .sr-label{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.25rem}
.sr-item .sr-value{font-size:1rem;font-weight:700}
.lean-result{margin-top:.85rem;display:flex;align-items:center;gap:.65rem}
.lean-result .lean-label{font-size:.78rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}

/* ── Empty/loading states ────────────────── */
#results{display:none}
.loading-state{text-align:center;padding:2.5rem;color:var(--muted);font-size:.95rem}
.error-state{background:rgba(240,112,112,.1);border:1px solid rgba(240,112,112,.3);border-radius:12px;padding:1rem 1.25rem;color:var(--red);font-size:.88rem}
</style>
</head>
<body>
<div class="app">

  <!-- Header -->
  <header class="header">
    <h1>CBB Matchup Analyzer</h1>
    <p class="sub">KenPom &amp; BartTorvik model analysis &mdash; not betting advice</p>
  </header>

  <!-- SECTION 1: Matchup Builder -->
  <section class="card section" id="builder">
    <h3 class="card-title">Matchup Builder</h3>
    <div class="builder-teams">
      <div class="team-field">
        <label for="ta-input">Team A (Away)</label>
        <div class="ac-wrap">
          <input id="ta-input" type="text" autocomplete="off" placeholder="Search team…" spellcheck="false"/>
          <ul class="ac-list" id="ta-list"></ul>
        </div>
      </div>
      <span class="at-sep">@</span>
      <div class="team-field">
        <label for="tb-input">Team B (Home)</label>
        <div class="ac-wrap">
          <input id="tb-input" type="text" autocomplete="off" placeholder="Search team…" spellcheck="false"/>
          <ul class="ac-list" id="tb-list"></ul>
        </div>
      </div>
    </div>
    <div class="builder-options">
      <div class="toggle-group">
        <label>Neutral Court?</label>
        <div class="toggle-btn">
          <button id="neutral-yes" type="button">Yes</button>
          <button id="neutral-no"  type="button" class="active">No</button>
        </div>
      </div>
      <div class="toggle-group">
        <label>Dampening Factors?</label>
        <div class="toggle-btn">
          <button id="damp-yes" type="button" class="active">Yes</button>
          <button id="damp-no"  type="button">No</button>
        </div>
      </div>
    </div>
    <div class="builder-actions">
      <button class="btn btn-primary" id="predict-btn" type="button">Predict</button>
      <button class="btn btn-secondary" id="reset-btn"  type="button">Reset</button>
    </div>
    <div id="builder-error"></div>
  </section>

  <!-- Results (shown after prediction) -->
  <div id="results">

    <!-- SECTION 2: Quick Summary Card -->
    <section class="summary-card section" id="summary-section">
      <div class="matchup-label" id="summary-title"></div>
      <div class="win-prob-bar-wrap">
        <div class="win-prob-teams">
          <div class="win-prob-team a">
            <span class="team-name" id="wp-name-a">Team A</span>
            <span class="pct" id="wp-pct-a">—</span>
          </div>
          <div class="win-prob-team b">
            <span class="team-name" id="wp-name-b">Team B</span>
            <span class="pct" id="wp-pct-b">—</span>
          </div>
        </div>
        <div class="win-prob-bar">
          <div class="seg-a" id="wp-bar-a" style="width:50%"></div>
          <div class="seg-b" id="wp-bar-b"></div>
        </div>
      </div>
      <div class="summary-grid">
        <div class="stat-box"><div class="s-label">Median Margin</div><div class="s-value" id="stat-margin">—</div></div>
        <div class="stat-box"><div class="s-label">Projected Total</div><div class="s-value" id="stat-total">—</div></div>
        <div class="stat-box"><div class="s-label">Confidence</div><div class="s-value" id="stat-confidence">—</div></div>
        <div class="stat-box"><div class="s-label">Model Spread</div><div class="s-value" id="stat-model-spread">—</div></div>
      </div>
      <div class="confidence-row">
        <span id="confidence-badge" class="badge toss-up">Toss-up</span>
        <span id="lean-badge" class="badge pass">Model Lean: —</span>
      </div>
    </section>

    <!-- SECTION 3: Source Projections Table -->
    <section class="card section" id="projections-section">
      <h3 class="card-title">Source Projections</h3>
      <table class="proj-table">
        <thead>
          <tr>
            <th>Source</th>
            <th id="th-a">Team A</th>
            <th id="th-b">Team B</th>
            <th>Diff</th>
            <th>Model Winner</th>
          </tr>
        </thead>
        <tbody id="proj-tbody"></tbody>
      </table>
    </section>

    <!-- SECTION 4 + 5: Adjustments & Histogram side-by-side -->
    <div class="two-col section">

      <!-- SECTION 4: Manual Adjustments -->
      <section class="card" id="adjustments-section">
        <h3 class="card-title">Manual Adjustments</h3>
        <div class="slider-group">
          <div class="slider-item">
            <label>Injury / Rest / Feel</label>
            <div class="slider-row">
              <input type="range" id="sl-injury" min="-5" max="5" step="0.5" value="0"/>
              <span class="slider-val" id="sv-injury">0</span>
            </div>
            <span class="adj-hint">Positive shifts model toward Team A</span>
          </div>
          <div class="slider-item">
            <label>Home Court / Crowd</label>
            <div class="slider-row">
              <input type="range" id="sl-hca" min="0" max="4" step="0.5" value="0"/>
              <span class="slider-val" id="sv-hca">0</span>
            </div>
            <span class="adj-hint" id="hca-hint">Adds to Team B home advantage</span>
          </div>
          <div class="slider-item">
            <label>Tempo Adjustment</label>
            <div class="slider-row">
              <input type="range" id="sl-tempo" min="-8" max="8" step="1" value="0"/>
              <span class="slider-val" id="sv-tempo">0</span>
            </div>
            <span class="adj-hint">Shifts projected total up/down</span>
          </div>
          <div class="slider-item">
            <label>Volatility</label>
            <div class="slider-row">
              <input type="range" id="sl-vol" min="-3" max="3" step="0.5" value="0"/>
              <span class="slider-val" id="sv-vol">0</span>
            </div>
            <span class="adj-hint">Widens/narrows outcome distribution</span>
          </div>
        </div>
      </section>

      <!-- SECTION 5: Margin Distribution -->
      <section class="card" id="histogram-section">
        <h3 class="card-title">Margin Distribution</h3>
        <div class="histogram-wrap" id="histogram-wrap"></div>
        <div class="histogram-legend">
          <div class="hist-leg-item"><div class="hist-dot" style="background:var(--blue)"></div><span id="hist-leg-a">Team A wins</span></div>
          <div class="hist-leg-item"><div class="hist-dot" style="background:var(--red)"></div><span id="hist-leg-b">Team B wins</span></div>
          <div class="hist-leg-item"><div class="hist-dot" style="background:var(--amber)"></div>Model spread</div>
        </div>
      </section>

    </div><!-- /.two-col -->

    <!-- SECTION 6: Spread Evaluator -->
    <section class="card section" id="evaluator-section">
      <h3 class="card-title">Spread Evaluator</h3>
      <div class="spread-row">
        <div class="spread-field">
          <label>Market Favors</label>
          <select id="ev-team">
            <option value="A">Team A</option>
            <option value="B">Team B</option>
          </select>
        </div>
        <div class="spread-field">
          <label>Spread (e.g. -5.5)</label>
          <input type="number" id="ev-spread" step="0.5" placeholder="-5.5"/>
        </div>
      </div>
      <div class="spread-result" id="ev-result" style="display:none">
        <div class="spread-result-grid">
          <div class="sr-item"><div class="sr-label">Model Spread</div><div class="sr-value" id="ev-model-spread">—</div></div>
          <div class="sr-item"><div class="sr-label">Market Spread</div><div class="sr-value" id="ev-market-spread">—</div></div>
          <div class="sr-item"><div class="sr-label">Edge</div><div class="sr-value" id="ev-edge">—</div></div>
        </div>
        <div class="lean-result">
          <span class="lean-label">Model Lean:</span>
          <span id="ev-lean-badge" class="badge pass">—</span>
        </div>
      </div>
      <div id="ev-placeholder" style="color:var(--muted);font-size:.85rem;margin-top:.25rem">
        Enter the market spread above to see the model lean.
      </div>
    </section>

  </div><!-- /#results -->
</div><!-- /.app -->

<script>
(function() {
  'use strict';

  // ── Teams & custom autocomplete ────────────────────────────
  var TEAMS = ${teamsJson};

  function setupAutocomplete(inputId, listId) {
    var input   = document.getElementById(inputId);
    var listEl  = document.getElementById(listId);
    var hiIndex = -1;

    function getMatches(query) {
      if (!query) return TEAMS.slice(0, 80);
      var q = query.toLowerCase();
      return TEAMS.filter(function(t) { return t.toLowerCase().indexOf(q) !== -1; }).slice(0, 80);
    }
    function renderList(matches) {
      listEl.innerHTML = '';
      hiIndex = -1;
      if (!matches.length) { listEl.classList.remove('open'); return; }
      matches.forEach(function(team) {
        var li = document.createElement('li');
        li.textContent = team;
        li.addEventListener('mousedown', function(e) {
          e.preventDefault();
          input.value = team;
          listEl.classList.remove('open');
        });
        listEl.appendChild(li);
      });
      listEl.classList.add('open');
    }
    function highlight(idx) {
      var items = listEl.querySelectorAll('li');
      items.forEach(function(li) { li.classList.remove('ac-hi'); });
      if (idx >= 0 && idx < items.length) { items[idx].classList.add('ac-hi'); items[idx].scrollIntoView({ block: 'nearest' }); }
    }
    input.addEventListener('focus', function() { renderList(getMatches(input.value)); });
    input.addEventListener('input', function() { renderList(getMatches(input.value)); });
    input.addEventListener('keydown', function(e) {
      var items = listEl.querySelectorAll('li');
      if (e.key === 'ArrowDown') { e.preventDefault(); hiIndex = Math.min(hiIndex + 1, items.length - 1); highlight(hiIndex); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); hiIndex = Math.max(hiIndex - 1, 0); highlight(hiIndex); }
      else if (e.key === 'Enter' && hiIndex >= 0) { e.preventDefault(); input.value = items[hiIndex].textContent; listEl.classList.remove('open'); }
      else if (e.key === 'Escape') { listEl.classList.remove('open'); }
    });
    input.addEventListener('blur', function() { setTimeout(function() { listEl.classList.remove('open'); }, 160); });
  }
  setupAutocomplete('ta-input', 'ta-list');
  setupAutocomplete('tb-input', 'tb-list');

  // ── App state ──────────────────────────────────────────────
  var state = {
    neutral     : false,
    useDampening: true,
    baseData    : null,
    sliders     : { injury: 0, hca: 0, tempo: 0, vol: 0 },
    simResult   : null,
  };

  // ── Toggle helpers ──────────────────────────────────────────
  function setToggle(yesId, noId, value) {
    document.getElementById(yesId).classList.toggle('active', value);
    document.getElementById(noId).classList.toggle('active', !value);
  }

  function syncNeutralUI() {
    var hcaInput = document.getElementById('sl-hca');
    var hcaHint  = document.getElementById('hca-hint');
    if (state.neutral) {
      hcaInput.disabled = true;
      hcaInput.value = '0';
      state.sliders.hca = 0;
      document.getElementById('sv-hca').textContent = '0';
      hcaHint.textContent = 'Disabled \u2014 neutral site';
    } else {
      hcaInput.disabled = false;
      hcaHint.textContent = 'Adds to Team B home advantage';
    }
  }

  document.getElementById('neutral-yes').addEventListener('click', function() {
    state.neutral = true; setToggle('neutral-yes','neutral-no', true); syncNeutralUI(); if (state.baseData) recompute();
  });
  document.getElementById('neutral-no').addEventListener('click', function() {
    state.neutral = false; setToggle('neutral-yes','neutral-no', false); syncNeutralUI(); if (state.baseData) recompute();
  });
  document.getElementById('damp-yes').addEventListener('click', function() {
    state.useDampening = true; setToggle('damp-yes','damp-no', true); if (state.baseData) runPredict();
  });
  document.getElementById('damp-no').addEventListener('click', function() {
    state.useDampening = false; setToggle('damp-yes','damp-no', false); if (state.baseData) runPredict();
  });

  // ── Slider wiring ───────────────────────────────────────────
  function wireSlider(inputId, valId, key) {
    var el = document.getElementById(inputId);
    var vl = document.getElementById(valId);
    el.addEventListener('input', function() {
      var v = parseFloat(el.value);
      state.sliders[key] = v;
      vl.textContent = v > 0 ? '+' + v : String(v);
      if (state.baseData) recompute();
    });
  }
  wireSlider('sl-injury','sv-injury','injury');
  wireSlider('sl-hca',   'sv-hca',   'hca');
  wireSlider('sl-tempo', 'sv-tempo', 'tempo');
  wireSlider('sl-vol',   'sv-vol',   'vol');

  // ── Spread evaluator wiring ─────────────────────────────────
  document.getElementById('ev-team').addEventListener('change',   function() { if (state.baseData) renderEvaluator(); });
  document.getElementById('ev-spread').addEventListener('input',  function() { if (state.baseData) renderEvaluator(); });

  // ── Reset ───────────────────────────────────────────────────
  document.getElementById('reset-btn').addEventListener('click', function() {
    document.getElementById('ta-input').value = '';
    document.getElementById('tb-input').value = '';
    state.neutral = false; state.useDampening = true; state.baseData = null; state.simResult = null;
    setToggle('neutral-yes','neutral-no', false);
    setToggle('damp-yes','damp-no', true);
    syncNeutralUI();
    ['sl-injury','sl-hca','sl-tempo','sl-vol'].forEach(function(id) { document.getElementById(id).value = '0'; });
    ['sv-injury','sv-hca','sv-tempo','sv-vol'].forEach(function(id) { document.getElementById(id).textContent = '0'; });
    state.sliders = { injury: 0, hca: 0, tempo: 0, vol: 0 };
    document.getElementById('ev-spread').value = '';
    document.getElementById('results').style.display = 'none';
    clearError();
  });

  // ── Predict ─────────────────────────────────────────────────
  document.getElementById('predict-btn').addEventListener('click', function() { runPredict(); });

  function runPredict() {
    var taInput = document.getElementById('ta-input').value.trim();
    var tbInput = document.getElementById('tb-input').value.trim();
    if (!taInput || !tbInput) { showError('Please enter both Team A and Team B.'); return; }
    clearError();
    var btn = document.getElementById('predict-btn');
    btn.disabled = true;
    btn.textContent = 'Loading\u2026';
    fetch('/api/matchup', {
      method : 'POST',
      headers: { 'content-type': 'application/json' },
      body   : JSON.stringify({ teamA: taInput, teamB: tbInput, neutral: state.neutral, useDampening: state.useDampening })
    })
    .then(function(res) {
      if (!res.ok) return res.json().catch(function() { return {}; }).then(function(e) { throw new Error(e.error || 'Server error ' + res.status); });
      return res.json();
    })
    .then(function(data) {
      if (!data.kenpom && !data.trank) { showError('No model data found for one or both teams. Check team names.'); return; }
      if (data.notes && data.notes.length) showError(data.notes.join(' '), true);
      state.baseData = data;
      recompute();
      document.getElementById('results').style.display = 'block';
    })
    .catch(function(err) { showError(err.message || 'Network error \u2014 could not reach the prediction API.'); })
    .finally(function() { btn.disabled = false; btn.textContent = 'Predict'; });
  }

  // ── Core computation ─────────────────────────────────────────
  function computeFinalSpread() {
    const c = state.baseData && state.baseData.consensus;
    if (!c) return null;
    let s = c.spread;
    s += state.sliders.injury;
    if (!state.neutral) s -= state.sliders.hca;
    return s;
  }

  function computeFinalTotal() {
    const c = state.baseData && state.baseData.consensus;
    if (!c) return null;
    return c.total + state.sliders.tempo;
  }

  // ── Simulation ────────────────────────────────────────────────
  function randn() {
    const u1 = Math.max(1e-14, Math.random());
    const u2 = Math.random();
    return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  }

  function simulate(finalSpread, stdAdj, n) {
    n = n || 5000;
    const STD_BASE = 11.0;
    const std = Math.max(3, STD_BASE + stdAdj);
    const margins = new Float32Array(n);
    let winsA = 0;
    for (let i = 0; i < n; i++) {
      const m = finalSpread + std * randn();
      margins[i] = m;
      if (m > 0) winsA++;
    }
    // Sort margins for median
    const sorted = Array.from(margins).sort((a, b) => a - b);
    const median = sorted[Math.floor(n / 2)];
    return { margins: sorted, median, winProbA: winsA / n, winProbB: 1 - winsA / n };
  }

  function buildHistData(sorted, bucketW) {
    bucketW = bucketW || 2;
    const MIN = -42, MAX = 42;
    const buckets = {};
    for (let v = MIN; v <= MAX; v += bucketW) buckets[v] = 0;
    for (const m of sorted) {
      if (m >= MIN && m <= MAX) {
        const b = Math.floor(m / bucketW) * bucketW;
        buckets[b] = (buckets[b] || 0) + 1;
      }
    }
    return Object.keys(buckets).map(k => ({ x: +k, count: buckets[k] }));
  }

  // ── Confidence & lean labels ──────────────────────────────────
  function confidenceLabel(probA) {
    const p = Math.max(probA, 1 - probA);
    if (p >= 0.70) return { label: 'Strong Lean',  cls: 'strong-lean' };
    if (p >= 0.60) return { label: 'Lean',         cls: 'lean' };
    if (p >= 0.55) return { label: 'Slight Lean',  cls: 'lean' };
    return              { label: 'Toss-up',         cls: 'toss-up' };
  }

  function leanLabel(edge, teamAName, teamBName) {
    const abs = Math.abs(edge);
    let tier, cls;
    if (abs < 1.0) {
      return { label: 'Pass',             cls: 'pass', teamName: null };
    } else if (abs < 2.5) {
      tier = 'Small Lean';
    } else if (abs < 4.0) {
      tier = 'Strong Lean';
    } else {
      tier = 'Very Strong Lean';
    }
    if (edge > 0) {
      cls = 'model-lean-a';
      return { label: tier + ' — ' + teamAName, cls, teamName: teamAName };
    } else {
      cls = 'model-lean-b';
      return { label: tier + ' — ' + teamBName, cls, teamName: teamBName };
    }
  }

  // ── Histogram SVG renderer ────────────────────────────────────
  function renderHistSVG(histData, finalSpread, teamAName, teamBName) {
    const W = 800, H = 190;
    const PL = 8, PR = 8, PT = 14, PB = 28;
    const plotW = W - PL - PR;
    const plotH = H - PT - PB;
    const maxCount = Math.max(1, ...histData.map(d => d.count));
    const xs = histData.map(d => d.x);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const xRange = maxX - minX || 1;
    const bucketW = histData.length > 1 ? histData[1].x - histData[0].x : 2;

    const toX  = x => PL + (x - minX) / xRange * plotW;
    const toH  = c => (c / maxCount) * plotH;
    const bpx  = (bucketW / xRange) * plotW;

    let bars = '';
    for (const { x, count } of histData) {
      if (!count) continue;
      const px = toX(x);
      const bh = toH(count);
      const py = PT + plotH - bh;
      const fill = x >= 0 ? '#4a90e2' : '#f07070';
      bars += '<rect x="' + px.toFixed(1) + '" y="' + py.toFixed(1) + '" width="' + Math.max(1, bpx - 1).toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="' + fill + '" opacity=".82"/>';
    }

    const z  = toX(0);
    const md = toX(Math.max(minX, Math.min(maxX, finalSpread)));

    let ticks = '';
    for (let v = -40; v <= 40; v += 10) {
      if (v < minX || v > maxX) continue;
      const tx = toX(v);
      const lbl = v > 0 ? '+' + v : String(v);
      ticks += '<line x1="' + tx.toFixed(1) + '" y1="' + (PT+plotH) + '" x2="' + tx.toFixed(1) + '" y2="' + (PT+plotH+4) + '" stroke="#3d5470" stroke-width="1"/>';
      ticks += '<text x="' + tx.toFixed(1) + '" y="' + (H - 2) + '" fill="#4a6080" font-size="10" text-anchor="middle">' + lbl + '</text>';
    }

    return '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg" style="display:block">'
      + '<line x1="' + PL + '" y1="' + PT + '" x2="' + PL + '" y2="' + (PT+plotH) + '" stroke="#1a2e4a" stroke-width="1"/>'
      + '<line x1="' + PL + '" y1="' + (PT+plotH) + '" x2="' + (PL+plotW) + '" y2="' + (PT+plotH) + '" stroke="#1a2e4a" stroke-width="1"/>'
      + bars
      + '<line x1="' + z.toFixed(1) + '" y1="' + PT + '" x2="' + z.toFixed(1) + '" y2="' + (PT+plotH) + '" stroke="#7a93b0" stroke-width="1.5" stroke-dasharray="4,3" opacity=".7"/>'
      + '<line x1="' + md.toFixed(1) + '" y1="' + PT + '" x2="' + md.toFixed(1) + '" y2="' + (PT+plotH) + '" stroke="#fbbf24" stroke-width="2"/>'
      + ticks
      + '<text x="' + (PL + 3) + '" y="' + (PT + 11) + '" fill="#f07070" font-size="10">\u2190 ' + esc(teamBName) + ' wins</text>'
      + '<text x="' + (PL + plotW - 3) + '" y="' + (PT + 11) + '" fill="#4a90e2" font-size="10" text-anchor="end">' + esc(teamAName) + ' wins \u2192</text>'
      + '</svg>';
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function fmtSpread(s, teamAName, teamBName) {
    if (s === null || s === undefined) return '—';
    const abs = Math.abs(s).toFixed(1);
    if (s > 0.05) return teamAName + ' -' + abs;
    if (s < -0.05) return teamBName + ' -' + abs;
    return 'Pick \'em';
  }

  // ── Full recompute (sliders changed or initial render) ─────────
  function recompute() {
    const data = state.baseData;
    if (!data) return;

    const finalSpread = computeFinalSpread();
    const finalTotal  = computeFinalTotal();
    if (finalSpread === null || finalTotal === null) return;

    const sim = simulate(finalSpread, state.sliders.vol, 5000);
    state.simResult = sim;

    renderSummary(data, sim, finalSpread, finalTotal);
    renderProjections(data);
    renderHistogram(sim.margins, finalSpread, data.teamA, data.teamB);
    renderEvaluator();
  }

  // ── Section 2: Summary ─────────────────────────────────────────
  function renderSummary(data, sim, finalSpread, finalTotal) {
    const tA = data.teamA, tB = data.teamB;
    document.getElementById('summary-title').textContent = tA + '  @  ' + tB + (data.neutral ? '  (Neutral)' : '');
    document.getElementById('wp-name-a').textContent = tA;
    document.getElementById('wp-name-b').textContent = tB;
    document.getElementById('hist-leg-a').textContent = tA + ' wins';
    document.getElementById('hist-leg-b').textContent = tB + ' wins';

    const pA = (sim.winProbA * 100).toFixed(1);
    const pB = (sim.winProbB * 100).toFixed(1);
    document.getElementById('wp-pct-a').textContent = pA + '%';
    document.getElementById('wp-pct-b').textContent = pB + '%';
    document.getElementById('wp-bar-a').style.width = pA + '%';

    // Median margin display
    const med = sim.median;
    const medStr = fmtSpread(med, tA, tB);
    document.getElementById('stat-margin').textContent = medStr;
    document.getElementById('stat-total').textContent  = finalTotal.toFixed(1);
    document.getElementById('stat-model-spread').textContent = fmtSpread(finalSpread, tA, tB);

    const conf = confidenceLabel(sim.winProbA);
    document.getElementById('stat-confidence').textContent = conf.label;
    const cb = document.getElementById('confidence-badge');
    cb.textContent = conf.label;
    cb.className   = 'badge ' + conf.cls;

    // Lean badge stays as-is until evaluator computes it
    renderLeanBadge(null, tA, tB);
  }

  function renderLeanBadge(lean) {
    const lb = document.getElementById('lean-badge');
    if (!lean) {
      lb.textContent = 'Model Lean: —';
      lb.className   = 'badge pass';
      return;
    }
    lb.textContent = 'Model Lean: ' + lean.label;
    lb.className   = 'badge ' + lean.cls;
  }

  // ── Section 3: Projections table ───────────────────────────────
  function renderProjections(data) {
    const tA = data.teamA, tB = data.teamB;
    document.getElementById('th-a').textContent = tA;
    document.getElementById('th-b').textContent = tB;

    const rows = [
      { label: 'KenPom',    proj: data.kenpom,    cls: '' },
      { label: 'BartTorvik', proj: data.trank,    cls: '' },
      { label: 'Consensus', proj: data.consensus, cls: 'consensus' },
    ];

    const tbody = document.getElementById('proj-tbody');
    tbody.innerHTML = rows.map(({ label, proj, cls }) => {
      if (!proj) {
        return '<tr class="' + cls + '"><td class="src-label">' + label + '</td><td colspan="4" style="color:var(--muted)">Data unavailable</td></tr>';
      }
      const diff    = Math.abs(proj.spread).toFixed(1);
      const winTeam = proj.spread > 0.05 ? tA : (proj.spread < -0.05 ? tB : null);
      const winCls  = proj.spread > 0.05 ? '' : 'b';
      const winCell = winTeam
        ? '<span class="winner-badge ' + winCls + '">' + esc(winTeam) + '</span>'
        : 'Pick \'em';
      return '<tr class="' + cls + '">'
        + '<td class="src-label">' + label + '</td>'
        + '<td class="score">' + proj.teamAScore.toFixed(1) + '</td>'
        + '<td class="score">' + proj.teamBScore.toFixed(1) + '</td>'
        + '<td>' + diff + '</td>'
        + '<td>' + winCell + '</td>'
        + '</tr>';
    }).join('');
  }

  // ── Section 5: Histogram ───────────────────────────────────────
  function renderHistogram(sortedMargins, finalSpread, teamAName, teamBName) {
    const histData = buildHistData(sortedMargins, 2);
    document.getElementById('histogram-wrap').innerHTML =
      renderHistSVG(histData, finalSpread, teamAName, teamBName);
  }

  // ── Section 6: Spread Evaluator ────────────────────────────────
  function renderEvaluator() {
    const data = state.baseData;
    if (!data) return;

    const spreadVal = parseFloat(document.getElementById('ev-spread').value);
    const evResult  = document.getElementById('ev-result');
    const evPlaceholder = document.getElementById('ev-placeholder');

    if (isNaN(spreadVal)) {
      evResult.style.display = 'none';
      evPlaceholder.style.display = 'block';
      renderLeanBadge(null);
      return;
    }

    evResult.style.display    = 'block';
    evPlaceholder.style.display = 'none';

    const finalSpread = computeFinalSpread();
    if (finalSpread === null) return;

    const spreadTeam = document.getElementById('ev-team').value;
    // Convert to Team A margin perspective:
    // "Team A -5.5" = Team A wins by 5.5 → userSpreadA = +5.5
    // "Team B -3"   = Team B wins by 3   → userSpreadA = -3
    const userSpreadA = (spreadTeam === 'A') ? -spreadVal : spreadVal;
    const edge        = finalSpread - userSpreadA;

    const tA = data.teamA, tB = data.teamB;
    const lean = leanLabel(edge, tA, tB);

    // Display model spread from Team A perspective
    document.getElementById('ev-model-spread').textContent  = fmtSpread(finalSpread, tA, tB);
    document.getElementById('ev-market-spread').textContent =
      (spreadTeam === 'A' ? tA : tB) + ' ' + (spreadVal > 0 ? '+' : '') + spreadVal.toFixed(1);
    const edgeAbs = Math.abs(edge);
    document.getElementById('ev-edge').textContent =
      (edge > 0.05 ? '+' : (edge < -0.05 ? '' : '±')) + edge.toFixed(1) + ' pts (' + (edge > 0.05 ? tA : (edge < -0.05 ? tB : 'even')) + ')';

    const lb = document.getElementById('ev-lean-badge');
    lb.textContent = lean.label;
    lb.className   = 'badge ' + lean.cls;

    renderLeanBadge(lean);
  }

  // ── Error display ───────────────────────────────────────────────
  function showError(msg, isWarning) {
    const el = document.getElementById('builder-error');
    el.innerHTML = '<div class="' + (isWarning ? 'error-state" style="background:var(--amber-d);border-color:rgba(251,191,36,.3);color:var(--amber)' : 'error-state') + '">' + esc(msg) + '</div>';
  }
  function clearError() {
    document.getElementById('builder-error').innerHTML = '';
  }

})();
</script>
</body>
</html>`;
};

// ── Worker entry point ─────────────────────────────────────────────────────────
export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response(renderHomePage(teamModels.teams), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    if (request.method === "GET" && url.pathname === "/api/teams") {
      return new Response(JSON.stringify({ teams: teamModels.teams }), { headers: jsonHeaders });
    }

    if (request.method === "POST" && url.pathname === "/api/matchup") {
      return handleMatchup(request);
    }

    return new Response("Not found", { status: 404 });
  },
};
