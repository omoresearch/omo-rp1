#!/usr/bin/env bash
# One-shot setup for the Omo RP1 MuJoCo sidecar.
#   ./setup.sh     create .venv, install requirements, fetch RoboParty's RP1 files (pinned commits) into upstream/,
#                  then run a 2-second smoke test
# Re-running is safe (skips what is already there).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"

say() { printf '\033[1;33m>>\033[0m %s\n' "$*"; }

# 1. Python env ------------------------------------------------------------------------------------
PY="${PYTHON:-python3}"
if command -v uv >/dev/null 2>&1; then
  [ -d .venv ] || uv venv -q --python "${UV_PYTHON:-3.12}" .venv
  say "installing requirements (uv)"
  uv pip install -q --python .venv/bin/python -r requirements.txt
else
  [ -d .venv ] || "$PY" -m venv .venv
  say "installing requirements (pip)"
  .venv/bin/pip install -q -r requirements.txt
fi

# 2. RoboParty's robot model + policies (fetched onto this machine, never redistributed) -----------
say "fetching RoboParty RP1 model, policies and configs into upstream/ (~81 MB, pinned commits)"
.venv/bin/python fetch_upstream.py

# 3. Smoke test (no HTTP) --------------------------------------------------------------------------
say "smoke test"
OMP_NUM_THREADS=1 .venv/bin/python - <<'EOF'
import rp1_sim
s = rp1_sim.RP1Sim(log=lambda *a: None, dance=False)
for _ in range(100):
    s.step()
print("ok: sim stepped 2 s, mode", s.mode, "fallen", s.fallen, "base z %.3f" % s.pos()[2])
EOF
say "done. Run:  OMP_NUM_THREADS=1 nice -n 10 .venv/bin/python server.py   (127.0.0.1:8797)"
say "the first start builds model_cache.json (the in-game mesh, ~40 s); later starts are instant"
