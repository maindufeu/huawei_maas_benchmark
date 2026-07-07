#!/usr/bin/env bash
# Dev launcher: FastAPI (auto-reload) on :8000 + Vite dev server (HMR) on :5173.
# Open http://127.0.0.1:5173  — Vite proxies /api to the backend.
set -euo pipefail
cd "$(dirname "$0")"

source .venv/bin/activate 2>/dev/null || { python3 -m venv .venv && source .venv/bin/activate && pip install -q -r requirements.txt; }

( python -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8000 ) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT

cd web
[ -d node_modules ] || npm install
echo "Frontend: http://127.0.0.1:5173   Backend: http://127.0.0.1:8000"
npm run dev
