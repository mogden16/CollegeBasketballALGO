import picksData from "../data/picks-of-day.json";

type Pick = {
  date: string;
  matchup: string;
  recommendedBet: string;
  modelSpread: string;
  vegasSpread: string | null;
  edge: number;
  confidence: string;
};

type PicksPayload = {
  asOf: string;
  picks: Pick[];
  reason: "ok" | "no_games_scheduled" | "no_cached_data" | "upstream_unavailable";
};

const payload = picksData as PicksPayload;

const jsonHeaders = { "content-type": "application/json; charset=utf-8" };

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
      </script>
    </body>
  </html>`;
};

const quickPredict = async (request: Request) => {
  const body = (await request.json()) as Record<string, string>;
  const homeOff = Number(body.homeOff);
  const homeDef = Number(body.homeDef);
  const awayOff = Number(body.awayOff);
  const awayDef = Number(body.awayDef);
  const pace = Number(body.pace);

  const possessions = Number.isFinite(pace) ? pace : 69;
  const homePPP = (homeOff + awayDef) / 200;
  const awayPPP = (awayOff + homeDef) / 200;

  const homeScore = Number((homePPP * possessions).toFixed(1));
  const awayScore = Number((awayPPP * possessions).toFixed(1));

  return new Response(
    JSON.stringify({
      homeScore,
      awayScore,
      projectedSpread: Number((awayScore - homeScore).toFixed(1)),
      projectedTotal: Number((homeScore + awayScore).toFixed(1)),
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
