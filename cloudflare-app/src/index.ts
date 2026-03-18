import modelsData from "../data/team-models.json";
import { normalizeTeamName } from "./teamName";
import { predictGame, buildConsensus, type TeamRatings, type MatchupResult, type KenPomTeamInfo } from "./matchup";
import { getNarrativeTemperature } from "./trendsProvider";

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

type NarrativeTemperatureBody = {
  teamA: string;
  teamB: string;
};

// ── Constants ────────────────────────────────────────────────────────────────
const teamModels  = modelsData as TeamModelsPayload;
const jsonHeaders = { "content-type": "application/json; charset=utf-8" } as const;
const KP_INFO_LABELS = [
  { key: "record", label: "Record", format: "record" },
  { key: "netRating", label: "Net Rating", format: "signed1" },
  { key: "offRating", label: "Off Rating", format: "fixed1" },
  { key: "defRating", label: "Def Rating", format: "fixed1" },
  { key: "adjTempo", label: "Adj Tempo", format: "fixed1" },
  { key: "luck", label: "Luck", format: "signed3" },
] as const;

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


const toFiniteNumber = (value: unknown): number | null => {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};

const buildKenPomTeamInfo = (teamName: string | null, team: TeamRatings | undefined): KenPomTeamInfo | null => {
  if (!teamName || !team) return null;
  return {
    team      : teamName,
    record    : typeof team.record === "string" ? team.record : null,
    netRating : toFiniteNumber(team.netRtg),
    offRating : toFiniteNumber(team.ortg ?? team.adjO),
    defRating : toFiniteNumber(team.drtg ?? team.adjD),
    adjTempo  : toFiniteNumber(team.adjT),
    luck      : toFiniteNumber(team.luck),
  };
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
    kenpomTeamInfo: {
      teamA: buildKenPomTeamInfo(resolvedAKp, resolvedAKp ? teamModels.kenpom[resolvedAKp] : undefined),
      teamB: buildKenPomTeamInfo(resolvedBKp, resolvedBKp ? teamModels.kenpom[resolvedBKp] : undefined),
    },
    notes,
  };

  return new Response(JSON.stringify(result), { headers: jsonHeaders });
};

// ── Narrative Temperature API handler ─────────────────────────────────────────
const handleNarrativeTemperature = async (request: Request): Promise<Response> => {
  let body: Partial<NarrativeTemperatureBody>;
  try {
    body = (await request.json()) as Partial<NarrativeTemperatureBody>;
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body." }), { status: 400, headers: jsonHeaders });
  }

  const teamAInput = String(body.teamA ?? "").trim();
  const teamBInput = String(body.teamB ?? "").trim();

  if (!teamAInput || !teamBInput) {
    return new Response(JSON.stringify({ error: "teamA and teamB are required." }), { status: 400, headers: jsonHeaders });
  }

  try {
    const narrative = await getNarrativeTemperature(teamAInput, teamBInput);
    return new Response(JSON.stringify(narrative), { headers: jsonHeaders });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to fetch trends data.";
    return new Response(JSON.stringify({
      error: message,
      teamA: { team: teamAInput, score: 0, label: "Neutral", badge: "Neutral", phrases: [], topPhrases: [] },
      teamB: { team: teamBInput, score: 0, label: "Neutral", badge: "Neutral", phrases: [], topPhrases: [] },
      volatility: { score: 0, label: "Low" },
      narrativeEdge: { label: "Narrative Edge: Neutral", strength: "neutral" },
      fetchedAt: new Date().toISOString(),
      sourceStatus: "unavailable",
      sourceNote: "Trends data unavailable right now.",
    }), { status: 200, headers: jsonHeaders });
  }
};

// ── Home page ─────────────────────────────────────────────────────────────────
const renderHomePage = (teams: string[]): string => {
  const teamsJson = JSON.stringify(teams);
  const kpInfoLabelsJson = JSON.stringify(KP_INFO_LABELS);
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

/* ── KenPom team info ───────────────────── */
.kenpom-compare-card{background:linear-gradient(180deg,rgba(16,30,52,.95),rgba(12,22,40,.95));border:1px solid var(--border2);border-radius:16px;padding:1.2rem}
.kenpom-compare-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}
@media(max-width:700px){.kenpom-compare-grid{grid-template-columns:1fr}}
.kenpom-team-panel{background:rgba(10,21,37,.88);border:1px solid var(--border);border-radius:14px;padding:1rem}
.kenpom-team-name{font-size:1rem;font-weight:700;margin-bottom:.85rem}
.kenpom-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}
.kp-metric{background:var(--card2);border:1px solid rgba(255,255,255,.05);border-radius:10px;padding:.65rem .75rem}
.kp-metric-label{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.25rem}
.kp-metric-value{font-size:1rem;font-weight:700}

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
.slider-polarity{display:flex;justify-content:space-between;align-items:center;font-size:.68rem;color:var(--muted);gap:.75rem}
.slider-polarity .neutral{color:var(--text)}

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

/* ── Narrative temperature ───────────────── */
.narrative-card{background:linear-gradient(180deg,rgba(16,30,52,.98),rgba(12,22,40,.98));border:1px solid var(--border2);border-radius:16px;padding:1.25rem}
.narrative-head{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:1rem}
.narrative-title-row{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}
.narrative-note{font-size:.8rem;color:var(--muted)}
.info-btn{width:24px;height:24px;border-radius:50%;border:1px solid var(--border2);background:#0a1525;color:var(--text);font-size:.82rem;font-weight:700;cursor:pointer}
.info-btn:hover{border-color:var(--blue);color:var(--blue)}
.narrative-info{display:none;margin-bottom:1rem;padding:.85rem 1rem;border-radius:12px;background:rgba(74,144,226,.09);border:1px solid rgba(74,144,226,.25);color:var(--text);font-size:.84rem;line-height:1.5}
.narrative-info.open{display:block}
.narrative-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;margin-bottom:1rem}
@media(max-width:700px){.narrative-grid{grid-template-columns:1fr}}
.narrative-team{background:rgba(10,21,37,.82);border:1px solid var(--border);border-radius:14px;padding:1rem}
.narrative-team-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;margin-bottom:.8rem}
.narrative-team-name{font-size:1rem;font-weight:700}
.narrative-score{font-size:1.5rem;font-weight:800;letter-spacing:-.03em}
.badge.hot{background:rgba(56,217,169,.14);color:var(--green);border:1px solid rgba(56,217,169,.28)}
.badge.warm{background:rgba(251,191,36,.12);color:var(--amber);border:1px solid rgba(251,191,36,.28)}
.badge.neutral{background:rgba(123,146,175,.15);color:#9ab0c8;border:1px solid rgba(123,146,175,.28)}
.badge.cool{background:rgba(74,144,226,.12);color:var(--blue);border:1px solid rgba(74,144,226,.28)}
.badge.risk{background:rgba(240,112,112,.13);color:var(--red);border:1px solid rgba(240,112,112,.28)}
.badge.vol-low,.badge.edge-neutral{background:rgba(123,146,175,.15);color:#9ab0c8;border:1px solid rgba(123,146,175,.28)}
.badge.vol-moderate,.badge.edge-slight{background:rgba(251,191,36,.12);color:var(--amber);border:1px solid rgba(251,191,36,.28)}
.badge.vol-high,.badge.edge-clear{background:rgba(240,112,112,.13);color:var(--red);border:1px solid rgba(240,112,112,.28)}
.phrase-chip-wrap{display:flex;flex-wrap:wrap;gap:.5rem}
.phrase-chip{padding:.38rem .65rem;border-radius:999px;background:var(--card2);border:1px solid rgba(255,255,255,.06);font-size:.76rem;color:var(--text)}
.phrase-chip.spike{border-color:rgba(74,144,226,.4);box-shadow:inset 0 0 0 1px rgba(74,144,226,.12)}
.phrase-chip small{color:var(--muted);margin-left:.3rem}
.narrative-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem;margin-bottom:.85rem}
@media(max-width:700px){.narrative-meta{grid-template-columns:1fr}}
.narrative-meta-card{background:var(--card2);border:1px solid var(--border);border-radius:12px;padding:.9rem 1rem}
.narrative-meta-card .label{font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:.35rem}
.narrative-footer{display:flex;justify-content:space-between;gap:.75rem;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:.8rem}
.narrative-status{min-height:1.2rem;font-size:.84rem;color:var(--muted)}
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

    <section class="kenpom-compare-card section" id="kenpom-team-info-section">
      <h3 class="card-title">KenPom Team Info</h3>
      <div class="kenpom-compare-grid" id="kenpom-team-info"></div>
    </section>

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
            <div class="slider-polarity"><span id="pol-injury-left">← Team A</span><span class="neutral">Neutral</span><span id="pol-injury-right">Team B →</span></div>
            <span class="adj-hint">Move left to help Team A. Move right to help Team B.</span>
          </div>
          <div class="slider-item">
            <label>Home Court / Crowd</label>
            <div class="slider-row">
              <input type="range" id="sl-hca" min="-4" max="4" step="0.5" value="0"/>
              <span class="slider-val" id="sv-hca">0</span>
            </div>
            <div class="slider-polarity"><span id="pol-hca-left">← Team A crowd edge</span><span class="neutral">Neutral</span><span id="pol-hca-right">Team B crowd edge →</span></div>
            <span class="adj-hint" id="hca-hint">Move left to help Team A. Move right to help Team B.</span>
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

    <!-- SECTION 6: Narrative Temperature -->
    <section class="narrative-card section" id="narrative-section">
      <div class="narrative-head">
        <div>
          <div class="narrative-title-row">
            <h3 class="card-title" style="margin-bottom:0">Narrative Temperature</h3>
            <button class="info-btn" id="narrative-info-btn" type="button" aria-expanded="false" aria-controls="narrative-info">i</button>
          </div>
          <div class="narrative-note">Soft signal based on Google Trends phrase activity</div>
        </div>
        <button class="btn btn-primary" id="narrative-scan-btn" type="button">Scan Trends</button>
      </div>
      <div class="narrative-info" id="narrative-info">
        We scan team-related Google Trends phrase activity across several categories:<br/>
        - Negative / risk: injury, questionable, suspended, slump, travel, short rest<br/>
        - Positive / momentum: returning, healthy, hot streak, momentum, breakout, dominant<br/>
        - Buzz / volatility: upset, odds, prediction, cinderella<br/>
        These are used as a soft narrative signal only.
      </div>
      <div class="narrative-status" id="narrative-status">Scan Google Trends style phrase activity for both teams to add a soft sentiment signal.</div>
      <div id="narrative-content" style="display:none">
        <div class="narrative-grid">
          <div class="narrative-team">
            <div class="narrative-team-head">
              <div>
                <div class="narrative-team-name" id="narrative-team-a-name">Team A</div>
                <div style="color:var(--muted);font-size:.82rem">Narrative temperature</div>
              </div>
              <div style="text-align:right">
                <div class="narrative-score" id="narrative-team-a-score">0.0</div>
                <span id="narrative-team-a-badge" class="badge neutral">Neutral</span>
              </div>
            </div>
            <div class="phrase-chip-wrap" id="narrative-team-a-phrases"></div>
          </div>
          <div class="narrative-team">
            <div class="narrative-team-head">
              <div>
                <div class="narrative-team-name" id="narrative-team-b-name">Team B</div>
                <div style="color:var(--muted);font-size:.82rem">Narrative temperature</div>
              </div>
              <div style="text-align:right">
                <div class="narrative-score" id="narrative-team-b-score">0.0</div>
                <span id="narrative-team-b-badge" class="badge neutral">Neutral</span>
              </div>
            </div>
            <div class="phrase-chip-wrap" id="narrative-team-b-phrases"></div>
          </div>
        </div>
        <div class="narrative-meta">
          <div class="narrative-meta-card">
            <div class="label">Volatility</div>
            <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
              <span id="narrative-volatility-badge" class="badge vol-low">Low</span>
              <span id="narrative-volatility-text">Volatility score 0.00</span>
            </div>
          </div>
          <div class="narrative-meta-card">
            <div class="label">Narrative Edge</div>
            <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
              <span id="narrative-edge-badge" class="badge edge-neutral">Neutral</span>
              <span id="narrative-edge-text">Narrative Edge: Neutral</span>
            </div>
          </div>
        </div>
        <div class="narrative-footer">
          <span id="narrative-source-note">Soft signal based on Google Trends phrase activity.</span>
          <span id="narrative-fetched-at"></span>
        </div>
      </div>
    </section>

    <!-- SECTION 7: Spread Evaluator -->
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
// Show any JS errors visually on the page
window.addEventListener('error', function(ev) {
  var d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#b91c1c;color:#fff;padding:.75rem 1rem;font-family:monospace;font-size:13px;z-index:9999;word-break:break-all';
  d.textContent = 'JS Error: ' + ev.message + ' (line ' + ev.lineno + ')';
  document.body.appendChild(d);
});

var TEAMS = ${teamsJson};
var KP_INFO_LABELS = ${kpInfoLabelsJson};

// ── Autocomplete ──────────────────────────────────────────────────────────────
function setupAC(inputId, listId) {
  var inp = document.getElementById(inputId);
  var ul  = document.getElementById(listId);
  var hi  = -1;
  if (!inp || !ul) return;

  function match(q) {
    if (!q) return TEAMS.slice(0, 80);
    q = q.toLowerCase();
    return TEAMS.filter(function(t) { return t.toLowerCase().indexOf(q) !== -1; }).slice(0, 80);
  }
  function show(list) {
    ul.innerHTML = ''; hi = -1;
    if (!list.length) { ul.classList.remove('open'); return; }
    list.forEach(function(name) {
      var li = document.createElement('li');
      li.textContent = name;
      li.addEventListener('mousedown', function(e) {
        e.preventDefault(); inp.value = name; ul.classList.remove('open');
      });
      ul.appendChild(li);
    });
    ul.classList.add('open');
  }
  function setHi(idx) {
    var items = ul.querySelectorAll('li');
    items.forEach(function(li) { li.classList.remove('ac-hi'); });
    if (idx >= 0 && items[idx]) { items[idx].classList.add('ac-hi'); items[idx].scrollIntoView({ block: 'nearest' }); }
  }
  inp.addEventListener('focus', function() { show(match(inp.value)); });
  inp.addEventListener('input', function() { show(match(inp.value)); });
  inp.addEventListener('keydown', function(e) {
    var items = ul.querySelectorAll('li');
    if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.min(hi + 1, items.length - 1); setHi(hi); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); hi = Math.max(hi - 1, 0); setHi(hi); }
    else if (e.key === 'Enter' && hi >= 0 && items[hi]) { e.preventDefault(); inp.value = items[hi].textContent; ul.classList.remove('open'); }
    else if (e.key === 'Escape') { ul.classList.remove('open'); }
  });
  inp.addEventListener('blur', function() { setTimeout(function() { ul.classList.remove('open'); }, 160); });
}

// ── App state ─────────────────────────────────────────────────────────────────
var appNeutral   = false;
var appDampening = true;
var appData      = null;
var appNarrative = null;
var appSliders   = { injury: 0, hca: 0, tempo: 0, vol: 0 };

// ── Init ──────────────────────────────────────────────────────────────────────
function initApp() {
  setupAC('ta-input', 'ta-list');
  setupAC('tb-input', 'tb-list');

  document.getElementById('neutral-yes').addEventListener('click', function() {
    appNeutral = true; setToggle('neutral-yes','neutral-no', true); syncNeutral(); if (appData) recompute();
  });
  document.getElementById('neutral-no').addEventListener('click', function() {
    appNeutral = false; setToggle('neutral-yes','neutral-no', false); syncNeutral(); if (appData) recompute();
  });
  document.getElementById('damp-yes').addEventListener('click', function() {
    appDampening = true; setToggle('damp-yes','damp-no', true); if (appData) runPredict();
  });
  document.getElementById('damp-no').addEventListener('click', function() {
    appDampening = false; setToggle('damp-yes','damp-no', false); if (appData) runPredict();
  });

  wireSlider('sl-injury','sv-injury','injury');
  wireSlider('sl-hca',   'sv-hca',   'hca');
  wireSlider('sl-tempo', 'sv-tempo', 'tempo');
  wireSlider('sl-vol',   'sv-vol',   'vol');

  document.getElementById('ev-team').addEventListener('change',  function() { if (appData) renderEval(); });
  document.getElementById('ev-spread').addEventListener('input', function() { if (appData) renderEval(); });
  document.getElementById('predict-btn').addEventListener('click', runPredict);
  document.getElementById('reset-btn').addEventListener('click',  resetAll);
  document.getElementById('narrative-scan-btn').addEventListener('click', scanNarrative);
  document.getElementById('narrative-info-btn').addEventListener('click', toggleNarrativeInfo);
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setToggle(yesId, noId, val) {
  document.getElementById(yesId).classList.toggle('active', val);
  document.getElementById(noId).classList.toggle('active', !val);
}
function syncNeutral() {
  var el = document.getElementById('sl-hca');
  var ht = document.getElementById('hca-hint');
  if (appNeutral) {
    el.disabled = true; el.value = '0'; appSliders.hca = 0;
    document.getElementById('sv-hca').textContent = '0';
    ht.textContent = 'Disabled \u2014 neutral site';
  } else {
    el.disabled = false;
    ht.textContent = 'Move left to help Team A. Move right to help Team B.';
  }
}
function wireSlider(inId, valId, key) {
  var el = document.getElementById(inId);
  var vl = document.getElementById(valId);
  el.addEventListener('input', function() {
    var v = parseFloat(el.value);
    appSliders[key] = v;
    vl.textContent = v > 0 ? '+' + v : String(v);
    if (appData) recompute();
  });
}
function resetAll() {
  document.getElementById('ta-input').value = '';
  document.getElementById('tb-input').value = '';
  appNeutral = false; appDampening = true; appData = null; appNarrative = null;
  setToggle('neutral-yes','neutral-no', false);
  setToggle('damp-yes','damp-no', true);
  syncNeutral();
  ['sl-injury','sl-hca','sl-tempo','sl-vol'].forEach(function(id) { document.getElementById(id).value = '0'; });
  ['sv-injury','sv-hca','sv-tempo','sv-vol'].forEach(function(id) { document.getElementById(id).textContent = '0'; });
  appSliders = { injury: 0, hca: 0, tempo: 0, vol: 0 };
  document.getElementById('ev-spread').value = '';
  document.getElementById('ev-team').innerHTML = '<option value="A">Team A</option><option value="B">Team B</option>';
  document.getElementById('kenpom-team-info').innerHTML = '';
  document.getElementById('results').style.display = 'none';
  document.getElementById('narrative-content').style.display = 'none';
  document.getElementById('narrative-status').textContent = 'Scan Google Trends style phrase activity for both teams to add a soft sentiment signal.';
  document.getElementById('narrative-source-note').textContent = 'Soft signal based on Google Trends phrase activity.';
  document.getElementById('narrative-fetched-at').textContent = '';
  clearErr();
}
function setBadge(id, text, cls) {
  var el = document.getElementById(id);
  if (el) { el.textContent = text; el.className = 'badge ' + cls; }
}
function fmtMargin(s, tA, tB) {
  if (s == null) return '\u2014';
  var a = Math.abs(s).toFixed(1);
  if (s > 0.05) return tA + ' -' + a;
  if (s < -0.05) return tB + ' -' + a;
  return "Pick 'em";
}
function htmlEsc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function formatKenPomValue(value, format) {
  if (value == null || value === '') return '\u2014';
  if (format === 'record') return String(value);
  var num = typeof value === 'number' ? value : parseFloat(value);
  if (!isFinite(num)) return '\u2014';
  if (format === 'signed3') return (num >= 0 ? '+' : '') + num.toFixed(3);
  if (format === 'signed1') return (num >= 0 ? '+' : '') + num.toFixed(1);
  return num.toFixed(1);
}
function renderKenPomTeamPanel(info) {
  if (!info) {
    return '<div class="kenpom-team-panel"><div class="kenpom-team-name">\u2014</div><div style="color:var(--muted);font-size:.84rem">KenPom details unavailable.</div></div>';
  }
  var metrics = KP_INFO_LABELS.map(function(item) {
    return '<div class="kp-metric"><div class="kp-metric-label">' + item.label + '</div><div class="kp-metric-value">' + formatKenPomValue(info[item.key], item.format) + '</div></div>';
  }).join('');
  return '<div class="kenpom-team-panel"><div class="kenpom-team-name">' + htmlEsc(info.team) + '</div><div class="kenpom-metrics">' + metrics + '</div></div>';
}
function syncSpreadEvaluatorOptions() {
  var sel = document.getElementById('ev-team');
  if (!sel || !appData) return;
  var current = sel.value || 'A';
  sel.innerHTML = ''
    + '<option value="A">' + htmlEsc(appData.teamA) + '</option>'
    + '<option value="B">' + htmlEsc(appData.teamB) + '</option>';
  sel.value = current === 'B' ? 'B' : 'A';
}
function showErr(msg, warn) {
  var bg = warn ? 'background:var(--amber-d);border-color:rgba(251,191,36,.3);color:var(--amber)'
                : 'background:rgba(240,112,112,.1);border-color:rgba(240,112,112,.3);color:var(--red)';
  document.getElementById('builder-error').innerHTML =
    '<div style="' + bg + ';border:1px solid;border-radius:10px;padding:.75rem 1rem;font-size:.85rem;margin-top:.6rem">' + htmlEsc(msg) + '</div>';
}
function clearErr() { document.getElementById('builder-error').innerHTML = ''; }

function toggleNarrativeInfo() {
  var panel = document.getElementById('narrative-info');
  var btn = document.getElementById('narrative-info-btn');
  var open = panel.classList.toggle('open');
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}
function badgeClassFromNarrative(label) {
  var key = String(label || 'neutral').toLowerCase();
  return key === 'hot' || key === 'warm' || key === 'cool' || key === 'risk' ? key : 'neutral';
}
function badgeClassFromVolatility(label) {
  var key = String(label || 'low').toLowerCase();
  return key === 'high' ? 'vol-high' : key === 'moderate' ? 'vol-moderate' : 'vol-low';
}
function badgeClassFromEdge(strength) {
  return strength === 'clear' ? 'edge-clear' : strength === 'slight' ? 'edge-slight' : 'edge-neutral';
}
function renderNarrativeTeam(prefix, summary) {
  document.getElementById(prefix + '-name').textContent = summary.team;
  document.getElementById(prefix + '-score').textContent = (summary.score > 0 ? '+' : '') + Number(summary.score || 0).toFixed(2);
  setBadge(prefix + '-badge', summary.label, badgeClassFromNarrative(summary.label));
  var phrases = summary.topPhrases && summary.topPhrases.length ? summary.topPhrases : summary.phrases.slice(0, 3);
  document.getElementById(prefix + '-phrases').innerHTML = phrases.length
    ? phrases.map(function(item) {
        return '<span class="phrase-chip' + (item.isSpiking ? ' spike' : '') + '">' + htmlEsc(summary.team + ' ' + (item.term || item.phrase)) + '<small>' + htmlEsc(item.category) + ' ' + item.trendScore.toFixed(0) + '</small></span>';
      }).join('')
    : '<span style="color:var(--muted);font-size:.82rem">No strong phrase signal detected.</span>';
}
function renderNarrative(data) {
  appNarrative = data;
  document.getElementById('narrative-content').style.display = 'block';
  renderNarrativeTeam('narrative-team-a', data.teamA);
  renderNarrativeTeam('narrative-team-b', data.teamB);
  setBadge('narrative-volatility-badge', data.volatility.label, badgeClassFromVolatility(data.volatility.label));
  document.getElementById('narrative-volatility-text').textContent = 'Volatility score ' + Number(data.volatility.score || 0).toFixed(2);
  var edgeLabel = data.narrativeEdge && data.narrativeEdge.label ? data.narrativeEdge.label.replace('Narrative Edge: ', '') : 'Neutral';
  setBadge('narrative-edge-badge', edgeLabel, badgeClassFromEdge(data.narrativeEdge && data.narrativeEdge.strength));
  document.getElementById('narrative-edge-text').textContent = data.narrativeEdge ? data.narrativeEdge.label : 'Narrative Edge: Neutral';
  document.getElementById('narrative-source-note').textContent = data.sourceNote || 'Soft signal based on Google Trends phrase activity.';
  document.getElementById('narrative-fetched-at').textContent = data.fetchedAt ? ('Updated ' + new Date(data.fetchedAt).toLocaleString()) : '';
  document.getElementById('narrative-status').textContent = data.sourceStatus === 'unavailable'
    ? 'Trends data unavailable right now.'
    : data.sourceStatus === 'partial'
      ? 'Partial trends signal loaded. Missing phrases were ignored.'
      : 'Narrative signal loaded. Use this as a soft supplemental read only.';
}
function scanNarrative() {
  var ta = document.getElementById('ta-input').value.trim();
  var tb = document.getElementById('tb-input').value.trim();
  if (!ta || !tb) { showErr('Please enter both teams before scanning trends.'); return; }
  var btn = document.getElementById('narrative-scan-btn');
  btn.disabled = true;
  btn.textContent = appNarrative ? 'Refreshing…' : 'Scanning…';
  document.getElementById('narrative-status').textContent = 'Scanning trends…';
  fetch('/api/narrative-temperature', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ teamA: ta, teamB: tb })
  })
    .then(function(res) { return res.json(); })
    .then(function(data) { renderNarrative(data); })
    .catch(function() {
      renderNarrative({
        teamA: { team: ta, score: 0, label: 'Neutral', badge: 'Neutral', phrases: [], topPhrases: [] },
        teamB: { team: tb, score: 0, label: 'Neutral', badge: 'Neutral', phrases: [], topPhrases: [] },
        volatility: { score: 0, label: 'Low' },
        narrativeEdge: { label: 'Narrative Edge: Neutral', strength: 'neutral' },
        fetchedAt: new Date().toISOString(),
        sourceStatus: 'unavailable',
        sourceNote: 'Trends data unavailable right now.'
      });
    })
    .finally(function() {
      btn.disabled = false;
      btn.textContent = appNarrative ? 'Refresh Trends' : 'Scan Trends';
    });
}

// ── Predict API call ──────────────────────────────────────────────────────────
function runPredict() {
  var ta = document.getElementById('ta-input').value.trim();
  var tb = document.getElementById('tb-input').value.trim();
  if (!ta || !tb) { showErr('Please enter both Team A and Team B.'); return; }
  clearErr();
  var btn = document.getElementById('predict-btn');
  btn.disabled = true; btn.textContent = 'Loading\u2026';
  fetch('/api/matchup', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ teamA: ta, teamB: tb, neutral: appNeutral, useDampening: appDampening })
  })
  .then(function(res) {
    if (!res.ok) return res.json().then(function(e) { throw new Error(e.error || 'Server error ' + res.status); });
    return res.json();
  })
  .then(function(data) {
    if (!data.kenpom && !data.trank) { showErr('No model data found. Check team names.'); return; }
    if (data.notes && data.notes.length) showErr(data.notes.join(' '), true);
    appData = data;
    recompute();
    document.getElementById('results').style.display = 'block';
    document.getElementById('narrative-status').textContent = 'Ready to scan a soft Google Trends narrative signal for this matchup.';
    document.getElementById('narrative-scan-btn').textContent = 'Scan Trends';
  })
  .catch(function(e) { showErr(e.message || 'Network error.'); })
  .finally(function() { btn.disabled = false; btn.textContent = 'Predict'; });
}

// ── Computation ───────────────────────────────────────────────────────────────
function getSpread() {
  var c = appData && appData.consensus;
  if (!c) return null;
  var s = c.spread - appSliders.injury;
  if (!appNeutral) s -= appSliders.hca;
  return s;
}
function getTotal() {
  var c = appData && appData.consensus;
  return c ? c.total + appSliders.tempo : null;
}

// ── Simulation ────────────────────────────────────────────────────────────────
function randNorm() {
  var u = Math.max(1e-14, Math.random());
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
}
function runSim(spread, volAdj) {
  var N = 5000, std = Math.max(3, 11 + (volAdj || 0));
  var margins = [], winsA = 0;
  for (var i = 0; i < N; i++) {
    var m = spread + std * randNorm();
    margins.push(m);
    if (m > 0) winsA++;
  }
  margins.sort(function(a, b) { return a - b; });
  return { margins: margins, median: margins[Math.floor(N / 2)], pA: winsA / N, pB: 1 - winsA / N };
}
function histBuckets(margins) {
  var W = 2, out = {};
  for (var v = -42; v <= 42; v += W) out[v] = 0;
  margins.forEach(function(m) {
    if (m >= -42 && m <= 42) { var b = Math.floor(m / W) * W; out[b] = (out[b] || 0) + 1; }
  });
  return Object.keys(out).map(function(k) { return { x: +k, n: out[k] }; });
}

// ── Labels ────────────────────────────────────────────────────────────────────
function confLabel(pA) {
  var p = Math.max(pA, 1 - pA);
  if (p >= 0.70) return { text: 'Strong Lean', cls: 'strong-lean' };
  if (p >= 0.60) return { text: 'Lean', cls: 'lean' };
  if (p >= 0.55) return { text: 'Slight Lean', cls: 'lean' };
  return { text: 'Toss-up', cls: 'toss-up' };
}
function leanResult(edge, tA, tB) {
  var a = Math.abs(edge);
  var tier = a < 1 ? null : a < 2.5 ? 'Small Lean' : a < 4 ? 'Strong Lean' : 'Very Strong Lean';
  if (!tier) return { text: 'Pass', cls: 'pass' };
  if (edge > 0) return { text: tier + ' \u2014 ' + tA, cls: 'model-lean-a' };
  return { text: tier + ' \u2014 ' + tB, cls: 'model-lean-b' };
}

// ── Recompute ─────────────────────────────────────────────────────────────────
function recompute() {
  if (!appData) return;
  var fs = getSpread(), ft = getTotal();
  if (fs === null || ft === null) return;
  var sim = runSim(fs, appSliders.vol);
  renderSummary(sim, fs, ft);
  renderTable();
  renderHist(sim.margins, fs);
  renderEval();
}

// ── Render: Summary ───────────────────────────────────────────────────────────
function renderSummary(sim, fs, ft) {
  var tA = appData.teamA, tB = appData.teamB;
  document.getElementById('kenpom-team-info').innerHTML =
    renderKenPomTeamPanel(appData.kenpomTeamInfo && appData.kenpomTeamInfo.teamA) +
    renderKenPomTeamPanel(appData.kenpomTeamInfo && appData.kenpomTeamInfo.teamB);
  syncSpreadEvaluatorOptions();
  document.getElementById('summary-title').textContent = tA + '  @  ' + tB + (appData.neutral ? '  (Neutral)' : '');
  document.getElementById('wp-name-a').textContent = tA;
  document.getElementById('wp-name-b').textContent = tB;
  document.getElementById('hist-leg-a').textContent = tA + ' wins (left)';
  document.getElementById('hist-leg-b').textContent = tB + ' wins (right)';
  var pA = (sim.pA * 100).toFixed(1), pB = (sim.pB * 100).toFixed(1);
  document.getElementById('wp-pct-a').textContent = pA + '%';
  document.getElementById('wp-pct-b').textContent = pB + '%';
  document.getElementById('wp-bar-a').style.width = pA + '%';
  document.getElementById('stat-margin').textContent = fmtMargin(sim.median, tA, tB);
  document.getElementById('stat-total').textContent  = ft.toFixed(1);
  document.getElementById('stat-model-spread').textContent = fmtMargin(fs, tA, tB);
  var c = confLabel(sim.pA);
  document.getElementById('stat-confidence').textContent = c.text;
  setBadge('confidence-badge', c.text, c.cls);
  setBadge('lean-badge', 'Model Lean: \u2014', 'pass');
}

// ── Render: Projections table ─────────────────────────────────────────────────
function renderTable() {
  var tA = appData.teamA, tB = appData.teamB;
  document.getElementById('th-a').textContent = tA;
  document.getElementById('th-b').textContent = tB;
  var rows = [
    { lbl: 'KenPom',    proj: appData.kenpom,    cls: '' },
    { lbl: 'BartTorvik', proj: appData.trank,    cls: '' },
    { lbl: 'Consensus', proj: appData.consensus, cls: 'consensus' }
  ];
  document.getElementById('proj-tbody').innerHTML = rows.map(function(r) {
    if (!r.proj) return '<tr class="' + r.cls + '"><td class="src-label">' + r.lbl + '</td><td colspan="4" style="color:var(--muted)">Unavailable</td></tr>';
    var diff = Math.abs(r.proj.spread).toFixed(1);
    var win  = r.proj.spread > 0.05 ? tA : (r.proj.spread < -0.05 ? tB : null);
    var wCls = win === tB ? ' b' : '';
    var wCell = win ? '<span class="winner-badge' + wCls + '">' + htmlEsc(win) + '</span>' : "Pick 'em";
    return '<tr class="' + r.cls + '"><td class="src-label">' + r.lbl + '</td>'
      + '<td class="score">' + r.proj.teamAScore.toFixed(1) + '</td>'
      + '<td class="score">' + r.proj.teamBScore.toFixed(1) + '</td>'
      + '<td>' + diff + '</td><td>' + wCell + '</td></tr>';
  }).join('');
}

// ── Render: Histogram ─────────────────────────────────────────────────────────
function renderHist(margins, fs) {
  var bkts = histBuckets(margins);
  var tA = appData.teamA, tB = appData.teamB;
  var W = 800, H = 190, PL = 8, PT = 14, PR = 8, PB = 28;
  var pw = W - PL - PR, ph = H - PT - PB;
  var maxN = 1;
  bkts.forEach(function(b) { if (b.n > maxN) maxN = b.n; });
  var xs = bkts.map(function(b) { return b.x; });
  var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  var xr = maxX - minX || 1;
  var bw = bkts.length > 1 ? bkts[1].x - bkts[0].x : 2;
  var bpx = (bw / xr) * pw;
  function toDisplayMargin(v) { return -v; }
  function toX(v) { return PL + (v - minX) / xr * pw; }
  var bars = '';
  bkts.forEach(function(b) {
    if (!b.n) return;
    var bh = (b.n / maxN) * ph;
    var displayMargin = toDisplayMargin(b.x);
    var barX = toX(displayMargin);
    var hoverLabel = displayMargin < -0.05 ? tA : (displayMargin > 0.05 ? tB : 'Either team');
    bars += '<rect x="' + barX.toFixed(1) + '" y="' + (PT + ph - bh).toFixed(1)
      + '" width="' + Math.max(1, bpx - 1).toFixed(1) + '" height="' + bh.toFixed(1)
      + '" fill="' + (displayMargin <= 0 ? '#4a90e2' : '#f07070') + '" opacity=".82">'
      + '<title>' + htmlEsc(hoverLabel) + ' outcome range: ' + (displayMargin > 0 ? '+' : '') + displayMargin.toFixed(0) + '</title></rect>';
  });
  var z = toX(0), md = toX(Math.max(minX, Math.min(maxX, toDisplayMargin(fs))));
  var ticks = '';
  for (var v = -40; v <= 40; v += 10) {
    if (v < minX || v > maxX) continue;
    var tx = toX(v);
    ticks += '<line x1="' + tx.toFixed(1) + '" y1="' + (PT+ph) + '" x2="' + tx.toFixed(1) + '" y2="' + (PT+ph+4) + '" stroke="#3d5470" stroke-width="1"/>';
    ticks += '<text x="' + tx.toFixed(1) + '" y="' + (H-2) + '" fill="#4a6080" font-size="10" text-anchor="middle">' + (v > 0 ? '+' + v : v) + '</text>';
  }
  document.getElementById('histogram-wrap').innerHTML =
    '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg" style="display:block">'
    + '<line x1="' + PL + '" y1="' + (PT+ph) + '" x2="' + (PL+pw) + '" y2="' + (PT+ph) + '" stroke="#1a2e4a" stroke-width="1"/>'
    + bars
    + '<line x1="' + z.toFixed(1) + '" y1="' + PT + '" x2="' + z.toFixed(1) + '" y2="' + (PT+ph) + '" stroke="#7a93b0" stroke-width="1.5" stroke-dasharray="4,3" opacity=".7"/>'
    + '<line x1="' + md.toFixed(1) + '" y1="' + PT + '" x2="' + md.toFixed(1) + '" y2="' + (PT+ph) + '" stroke="#fbbf24" stroke-width="2"/>'
    + ticks
    + '<text x="' + (PL+3) + '" y="' + (PT+11) + '" fill="#4a90e2" font-size="10">\u2190 ' + htmlEsc(tA) + ' wins</text>'
    + '<text x="' + (PL+pw-3) + '" y="' + (PT+11) + '" fill="#f07070" font-size="10" text-anchor="end">' + htmlEsc(tB) + ' wins \u2192</text>'
    + '</svg>';
}

// ── Render: Spread Evaluator ──────────────────────────────────────────────────
function renderEval() {
  if (!appData) return;
  var val = parseFloat(document.getElementById('ev-spread').value);
  var res = document.getElementById('ev-result');
  var ph  = document.getElementById('ev-placeholder');
  if (isNaN(val)) {
    res.style.display = 'none'; ph.style.display = 'block';
    setBadge('lean-badge', 'Model Lean: \u2014', 'pass'); return;
  }
  res.style.display = 'block'; ph.style.display = 'none';
  var fs = getSpread(); if (fs === null) return;
  var team  = document.getElementById('ev-team').value;
  var userA = team === 'A' ? -val : val;
  var edge  = fs - userA;
  var tA = appData.teamA, tB = appData.teamB;
  var lean = leanResult(edge, tA, tB);
  document.getElementById('ev-model-spread').textContent  = fmtMargin(fs, tA, tB);
  document.getElementById('ev-market-spread').textContent = (team === 'A' ? tA : tB) + ' ' + (val > 0 ? '+' : '') + val.toFixed(1);
  document.getElementById('ev-edge').textContent = (edge > 0.05 ? '+' : '') + edge.toFixed(1) + ' pts (' + (edge > 0.05 ? tA : edge < -0.05 ? tB : 'even') + ')';
  setBadge('ev-lean-badge', lean.text, lean.cls);
  setBadge('lean-badge', 'Model Lean: ' + lean.text, lean.cls);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
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

    if (request.method === "POST" && url.pathname === "/api/narrative-temperature") {
      return handleNarrativeTemperature(request);
    }

    return new Response("Not found", { status: 404 });
  },
};
