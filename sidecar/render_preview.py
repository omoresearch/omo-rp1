#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-only: see sidecar/LICENSE).
"""Offscreen preview frames -> preview/*.png (MUJOCO_GL=egl, falls back to osmesa)."""
import os
import sys

os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("OMP_NUM_THREADS", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mujoco  # noqa: E402
from PIL import Image  # noqa: E402

import rp1_sim as rs  # noqa: E402

out = os.path.join(HERE, "preview")
os.makedirs(out, exist_ok=True)
sim = rs.RP1Sim(log=print, dance=False, visual=True)
r = mujoco.Renderer(sim.model, 480, 640)
cam = mujoco.MjvCamera()
cam.lookat[:] = [0, 0, 0.6]
cam.distance = 2.4
cam.elevation = -12
opt = mujoco.MjvOption()


def shot(name, az):
    cam.lookat[:2] = sim.pos()[:2]
    cam.azimuth = az
    r.update_scene(sim.data, cam, opt)
    Image.fromarray(r.render()).save(os.path.join(out, name))
    print("wrote", name)


for _ in range(50):
    sim.step()
shot("stand_front.png", 180)
shot("stand_side.png", 90)
sim.command("walk")
for _ in range(150):
    sim.step()
shot("walk_side.png", 90)
sim.command("reset")
for _ in range(50):
    sim.step()
sim.command("wave")
for _ in range(60):  # ~1.2 s: arm up
    sim.step()
shot("wave_front.png", 180)
