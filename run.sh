#!/usr/bin/env bash
# Production launcher: build the React app, then serve app + API from one FastAPI process.
# Open http://127.0.0.1:8000 when it's up.
set -euo pipefail
cd "$(dirname "$0")"

# --- Python backend ---
if [ ! -d ".venv" ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# --- React frontend (build once) ---
if command -v npm >/dev/null 2>&1; then
  if [ ! -d "web/node_modules" ]; then
    echo "Installing web deps…"
    (cd web && npm install)
  fi
  echo "Building web…"
  (cd web && npm run build)
else
  echo "WARNING: npm not found — serving whatever is in web/dist (build it on a machine with Node)."
fi

echo "Serving on http://127.0.0.1:8000  (Ctrl-C to stop)"
exec python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
