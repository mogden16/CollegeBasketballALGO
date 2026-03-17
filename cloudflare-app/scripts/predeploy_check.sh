#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8787}"
DATE_CHECK="${DATE_CHECK:-$(date -u +%Y-%m-%d)}"

cleanup() {
  if [[ -n "${WRANGLER_PID:-}" ]]; then
    kill "$WRANGLER_PID" >/dev/null 2>&1 || true
    wait "$WRANGLER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "[1/3] Wrangler dry-run bundle check"
npx wrangler deploy --dry-run >/tmp/predeploy_wrangler_dryrun.log 2>&1

echo "[2/3] Launching local worker on port $PORT"
npx wrangler dev --local --port "$PORT" >/tmp/predeploy_wrangler_dev.log 2>&1 &
WRANGLER_PID=$!
for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[3/3] API smoke checks"

echo "  - /api/picks?date=$DATE_CHECK"
PICKS_RESPONSE="$(curl -fsS "http://127.0.0.1:${PORT}/api/picks?date=${DATE_CHECK}")"
python3 - <<'PY' "$PICKS_RESPONSE"
import json, sys
payload = json.loads(sys.argv[1])
required = {"selectedDate", "picks", "source"}
missing = required - payload.keys()
if missing:
    raise SystemExit(f"Missing required keys: {sorted(missing)}")
if not isinstance(payload["picks"], list):
    raise SystemExit("`picks` is not a list")
print(f"OK: picks={len(payload['picks'])}, source={payload.get('source')}, reason={payload.get('reason')}")
PY

echo "  - /api/quick-predict"
QP_RESPONSE="$(curl -fsS -X POST "http://127.0.0.1:${PORT}/api/quick-predict" -H 'content-type: application/json' -d '{"homeTeam":"Duke","awayTeam":"UNC","neutral":"false"}')"
python3 - <<'PY' "$QP_RESPONSE"
import json, sys
payload = json.loads(sys.argv[1])
for key in ("homeTeam", "awayTeam", "kenpom", "trank", "notes"):
    if key not in payload:
        raise SystemExit(f"Missing key: {key}")
print("OK: quick-predict payload keys validated")
PY

echo "Pre-deploy checks passed."
