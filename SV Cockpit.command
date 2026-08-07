#!/bin/bash
# SV Cockpit — double-clic : synchronise avec GitHub (état réel des publications),
# démarre le moteur si besoin, puis ouvre la page.
cd "$(dirname "$0")"
git stash -q 2>/dev/null
git pull -q origin main 2>/dev/null
git stash pop -q 2>/dev/null
if ! curl -s --max-time 2 http://localhost:8765/ >/dev/null 2>&1; then
  nohup python3 engine/serve.py >/tmp/sv-cockpit.log 2>&1 &
  disown
  for i in $(seq 1 20); do
    curl -s --max-time 1 http://localhost:8765/ >/dev/null 2>&1 && break
    sleep 0.5
  done
fi
open "http://localhost:8765"
exit 0
