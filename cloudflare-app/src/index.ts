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
};

const payload = picksData as PicksPayload;

const jsonHeaders = { "content-type": "application/json; charset=utf-8" };

const renderHomePage = () => {
  const cards = payload.picks
    .map(
      (pick) => `
      <article class="pick-card">
        <h3>${pick.matchup}</h3>
        <p><strong>Recommended:</strong> ${pick.recommendedBet}</p>
        <p><strong>Model spread:</strong> ${pick.modelSpread}</p>
        <p><strong>Vegas spread:</strong> ${pick.vegasSpread ?? "N/A"}</p>
        <p><strong>Edge:</strong> ${pick.edge}</p>
        <span class="tag ${pick.confidence.toLowerCase()}">${pick.confidence}</span>
      </article>
    `,
    )
    .join("");

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
        .pick-card { background:#1e293b; border-radius:10px; padding:.8rem; border:1px solid #334155; }
        .tag { display:inline-block; margin-top:.4rem; border-radius: 99px; padding: .15rem .55rem; font-size:.75rem; font-weight:bold; }
        .high{ background:#166534; } .medium{ background:#854d0e; } .low{ background:#1d4ed8; }
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
        <p>As of ${payload.asOf}. Powered by your model output.</p>
        <section class="grid">${cards}</section>

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
          result.textContent = awayTeam + ' vs ' + homeTeam + ' — Projected team totals: ' + awayTeam + ' ' + data.awayScore + ', ' + homeTeam + ' ' + data.homeScore + '. Overall total: ' + data.projectedTotal;
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
      return new Response(JSON.stringify(payload), { headers: jsonHeaders });
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
