#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-or-later: see sidecar/LICENSE).
"""Random-command stress test (headless): python stress.py [sim_minutes] [seed]. Reports falls / auto-resets."""
import os
import random
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rp1_sim as rs  # noqa: E402

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 5
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
rng = random.Random(seed)
sim = rs.RP1Sim(log=lambda *a: print("   ", *a))
cmds = ["walk", "walk", "stop", "left", "right", "follow", "follow", "wave", "wave", "dance", "walk_v", "talk", "talk", "talk_off"]
t_end = minutes * 60
t0 = time.perf_counter()
n = 0
while sim.t < t_end:
    c = rng.choice(cmds)
    if c in ("left", "right"):
        sim.command("turn", dir=c)
    elif c == "walk_v":
        sim.command("walk", vx=rng.uniform(-0.3, 0.45), vy=rng.uniform(-0.3, 0.3), wz=rng.uniform(-0.6, 0.6))
    elif c == "talk":  # voice talk gesture (only applied while standing)
        sim.command("talk", on=True, level=rng.uniform(0.2, 1.0))
    elif c == "talk_off":
        sim.command("talk", on=False)
    elif c == "follow":
        sim.set_player(rng.uniform(-8, 8), rng.uniform(-8, 8))
        sim.command("follow")
    else:
        sim.command(c)
    n += 1
    for _ in range(int(rng.uniform(1.0, 8.0) / rs.CONTROL_DT)):
        sim.step()
print(f"{n} commands over {sim.t / 60:.1f} sim-min ({time.perf_counter() - t0:.0f}s wall): falls={sim.falls} "
      f"resets={sim.resets} waves={sim.waves} talks={sim.talks}")
