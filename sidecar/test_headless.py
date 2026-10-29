#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-only: see sidecar/LICENSE).
"""Headless (no HTTP) test of the RP1 sim, faster than real time. Prints PASS/FAIL per check + SUMMARY.

Checks: stand 20 s, walk ~0.4 m/s forward (straightness), turn left/right ~90 deg, follow the player and stop
within ~1 m, leash, reset, fall detection + auto-reset after 3 s, wave (motion tracking), dance (ufo), real-time
budget per 50 Hz step, model.json sanity.
"""

import math
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import model_export  # noqa: E402
import rp1_sim as rs  # noqa: E402

Z_MIN = 0.55        # standing base_link z ~0.77; fallen < 0.40
UPRIGHT_GZ = -0.85  # projected gravity z (-1 = upright); -0.85 = 32 deg tilt

results = []


def check(name, ok, detail):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


class Tracker:
    def __init__(self, sim):
        self.sim = sim
        self.min_z = 9.0
        self.max_gz = -1.0
        self.max_r = 0.0

    def step(self, seconds, until=None):
        s = self.sim
        for _ in range(int(round(seconds / rs.CONTROL_DT))):
            s.step()
            p = s.pos()
            self.min_z = min(self.min_z, float(p[2]))
            self.max_gz = max(self.max_gz, float(s.grav()[2]))
            self.max_r = max(self.max_r, math.hypot(p[0], p[1]))
            if until is not None and until():
                return


def settle(sim, sec=1.0):
    for _ in range(int(sec / rs.CONTROL_DT)):
        sim.step()


def main():
    t_wall = time.perf_counter()
    sim = rs.RP1Sim(log=lambda *a: print("   ", *a))
    print(f"gains={sim.gains} speed_gain={rs.SPEED_GAIN} policies={[p.file for p in sim.policies.values()]}")

    # ---------------------------------------------------------------- stand 20 s
    tr = Tracker(sim)
    f0 = sim.falls
    tr.step(20.0)
    p = sim.pos()
    check("stand 20 s", sim.falls == f0 and tr.min_z > Z_MIN and tr.max_gz < UPRIGHT_GZ and math.hypot(p[0], p[1]) < 0.1,
          f"falls={sim.falls - f0} min z={tr.min_z:.3f} max gz={tr.max_gz:.3f} drift={math.hypot(p[0], p[1]):.3f} m")

    # ---------------------------------------------------------------- talk gesture (voice) while standing
    ei = rs.JOINTS.index("right_elbow_joint")
    wi = rs.JOINTS.index("waist_yaw_joint")
    sim.command("talk", on=True, level=1.0)
    tr = Tracker(sim)
    f0 = sim.falls
    p0 = sim.pos()[:2].copy()
    el, wy = [], []
    for _ in range(int(25.0 / rs.CONTROL_DT)):
        tr.step(rs.CONTROL_DT)
        el.append(float(sim.data.qpos[sim.jq[ei]]))
        wy.append(float(sim.data.qpos[sim.jq[wi]]))
    el_rng = max(el[100:]) - min(el[100:])
    wy_rng = max(wy[100:]) - min(wy[100:])
    drift = float(np.linalg.norm(sim.pos()[:2] - p0))
    check("talk 25 s while standing: gestures, no fall, feet stay put",
          sim.falls == f0 and tr.min_z > Z_MIN and tr.max_gz < UPRIGHT_GZ and drift < 0.15 and el_rng > 0.1 and wy_rng > 0.04
          and sim.talk_env > 0.99 and sim.talks == 1,
          f"falls={sim.falls - f0} min z={tr.min_z:.3f} max gz={tr.max_gz:.3f} drift={drift:.3f} m, "
          f"right elbow range={el_rng:.3f} rad, waist yaw range={wy_rng:.3f} rad, env={sim.talk_env:.2f}")
    sim.command("walk")
    tr = Tracker(sim)
    tr.step(3.0)
    env_walk = sim.talk_env
    sim.command("stop")
    tr.step(2.0)
    env_back = sim.talk_env
    sim.command("talk", on=False)
    tr.step(1.0)
    check("talk: suppressed while walking, resumes when standing, off ramps out",
          env_walk == 0.0 and env_back > 0.9 and sim.talk_env == 0.0 and not sim.talk_on and sim.falls == f0
          and tr.min_z > Z_MIN,
          f"env walking={env_walk:.2f}, standing again={env_back:.2f}, after off={sim.talk_env:.2f}, falls={sim.falls - f0}")
    sim.command("reset")
    settle(sim, 1.0)

    # ---------------------------------------------------------------- walk forward
    sim.leash = 100.0
    y0 = sim.yaw()
    sim.command("walk")
    tr = Tracker(sim)
    tr.step(2.0)
    a = sim.pos()[:2].copy()
    tr.step(10.0)
    b = sim.pos()[:2].copy()
    v = (b - a) / 10.0
    fwd = np.array([math.cos(y0), math.sin(y0)])
    speed = float(v @ fwd)
    lat = float(abs(fwd[0] * (b - a)[1] - fwd[1] * (b - a)[0]))
    check("walk forward ~0.4 m/s", 0.3 <= speed <= 0.5 and sim.falls == f0,
          f"forward speed={speed:.3f} m/s (cmd {rs.WALK_VX} nominal, policy cmd {rs.WALK_VX * rs.SPEED_GAIN:.2f}), "
          f"lateral drift={lat:.2f} m over 10 s, yaw change={math.degrees(rs.wrap(sim.yaw() - y0)):+.1f} deg, falls={sim.falls - f0}")
    check("walk stays upright", tr.min_z > Z_MIN and tr.max_gz < UPRIGHT_GZ, f"min z={tr.min_z:.3f} max gz={tr.max_gz:.3f}")
    sim.command("stop")
    tr.step(3.0)
    p0 = sim.pos()[:2].copy()
    tr.step(2.0)
    check("stop: stands still", np.linalg.norm(sim.pos()[:2] - p0) < 0.1 and sim.mode == "stand",
          f"moved {np.linalg.norm(sim.pos()[:2] - p0):.3f} m in 2 s after stop")

    # ---------------------------------------------------------------- turns
    for d, sign in (("left", 1), ("right", -1)):
        y0 = sim.yaw()
        t0 = sim.t
        sim.command("turn", dir=d)
        tr.step(10.0, until=lambda: sim.mode != "turn")
        dt = sim.t - t0
        tr.step(1.0)
        dy = math.degrees(rs.wrap(sim.yaw() - y0))
        check(f"turn {d} ~90 deg then stop", 70 <= sign * dy <= 110 and sim.mode == "stand" and sim.falls == f0,
              f"dyaw={dy:+.1f} deg in {dt:.2f} s, mode after={sim.mode}")

    # ---------------------------------------------------------------- follow
    sim.leash = 6.0
    sim.set_player(-2.5, 2.0)
    sim.command("follow")
    tr.step(25.0)
    p = sim.pos()
    dfol = math.hypot(p[0] + 2.5, p[1] - 2.0)
    check("follow reaches player and stops ~1 m away", 0.6 < dfol < 1.4 and sim.falls == f0,
          f"dist to player={dfol:.2f} m, cmd={np.round(sim.cmd, 2).tolist()}")
    sim.set_player(10.0, 0.0)  # outside the 6 m leash
    tr = Tracker(sim)
    tr.step(30.0)
    check("follow target clamped inside the leash", tr.max_r <= sim.leash and sim.falls == f0,
          f"max dist from origin={tr.max_r:.2f} m (leash {sim.leash}), now at r={math.hypot(*sim.pos()[:2]):.2f}")

    # ---------------------------------------------------------------- leash on a straight walk
    sim.command("reset")
    settle(sim)
    sim.leash = 3.0
    tr = Tracker(sim)
    sim.command("walk")
    tr.step(40.0)
    check("leash 3 m: steers back on a 40 s straight walk", tr.max_r <= 3.0 and sim.falls == f0,
          f"max dist={tr.max_r:.2f} m, falls={sim.falls - f0}")
    sim.leash = 6.0

    # ---------------------------------------------------------------- reset
    sim.command("walk")
    settle(sim, 2.0)
    r0 = sim.resets
    sim.command("reset")
    p = sim.pos()
    check("reset respawns upright at origin", sim.resets == r0 + 1 and math.hypot(p[0], p[1]) < 1e-6 and sim.mode == "stand"
          and sim.grav()[2] < -0.999, f"pos=({p[0]:.3f},{p[1]:.3f},{p[2]:.3f}) mode={sim.mode}")
    settle(sim, 3.0)
    check("stable after reset", not sim.fallen and sim.pos()[2] > Z_MIN, f"z={sim.pos()[2]:.3f}")

    # ---------------------------------------------------------------- wave
    f1 = sim.falls
    w = sim.policies["wave"]
    sim.command("wave")
    errs, peak = [], -9.0
    t0 = sim.t
    tr = Tracker(sim)
    while sim.mode == "wave" and sim.t - t0 < 10:
        fr = w.frame
        tr.step(rs.CONTROL_DT)
        if sim.mode == "wave":
            q = sim.data.qpos[sim.jq]
            errs.append(float(np.abs(q - w.mpos[min(fr, w.nframes - 1)]).mean()))
            peak = max(peak, float(q[19]))  # right_shoulder_pitch (arm raised)
    dur = sim.t - t0
    tr.step(3.0)
    check("wave plays once, tracks the reference, then back to stand",
          sim.mode == "stand" and sim.falls == f1 and np.mean(errs) < 0.15 and peak > 1.5 and sim.active == "loco",
          f"{dur:.2f} s ({w.nframes} frames @ {w.fps} fps), mean |q-ref|={np.mean(errs):.3f} rad, "
          f"right shoulder pitch peak={peak:.2f} rad, falls={sim.falls - f1}, min z={tr.min_z:.3f}")

    # ---------------------------------------------------------------- dance (ufo)
    if "dance" in sim.policies:
        f1 = sim.falls
        sim.command("reset")
        settle(sim)
        t0 = sim.t
        sim.command("dance")
        tr = Tracker(sim)
        tr.step(rs.DANCE_S + 5, until=lambda: sim.mode != "dance")
        dur = sim.t - t0
        tr.step(4.0)
        check("dance (policy_ufo) segment without falling, back to stand",
              sim.falls == f1 and sim.mode in ("stand", "home") and tr.min_z > rs.FALL_Z and tr.max_gz < -0.7,
              f"{dur:.1f} s danced, falls={sim.falls - f1}, min z={tr.min_z:.3f} (deep squats), max gz={tr.max_gz:.2f}, max r={tr.max_r:.2f} m, mode={sim.mode}")

    # ---------------------------------------------------------------- fall + auto-reset
    sim.command("reset")
    settle(sim)
    d = sim.data
    d.qvel[sim.fv:sim.fv + 3] = [2.5, 0, 0]   # violent shove at the base
    d.qvel[sim.fv + 3:sim.fv + 6] = [0, 4.0, 0]
    f1, r1 = sim.falls, sim.resets
    t0 = sim.t
    t_fall = None
    while sim.t - t0 < 8 and sim.resets == r1:
        sim.step()
        if t_fall is None and sim.falls > f1:
            t_fall = sim.t
    ok = sim.falls == f1 + 1 and sim.resets == r1 + 1 and t_fall is not None
    check("fall detected + auto-reset after ~3 s", ok and 2.9 <= sim.t - t_fall <= 3.3,
          f"fall at +{(t_fall or 0) - t0:.2f} s, reset {sim.t - (t_fall or sim.t):.2f} s later" if ok else
          f"falls={sim.falls - f1} resets={sim.resets - r1}")
    settle(sim, 3.0)
    check("upright after auto-reset", not sim.fallen and sim.pos()[2] > Z_MIN, f"z={sim.pos()[2]:.3f}")

    # ---------------------------------------------------------------- real-time budget
    sim.command("walk")
    n = 250
    t = time.perf_counter()
    for _ in range(n):
        sim.step()
    ms = (time.perf_counter() - t) / n * 1000
    check("step cost << 20 ms (50 Hz real time, one thread)", ms < 8.0, f"{ms:.2f} ms per 50 Hz step (walk, policy.onnx)")
    if "dance" in sim.policies:
        sim.command("dance")
        t = time.perf_counter()
        for _ in range(n):
            sim.step()
        ms = (time.perf_counter() - t) / n * 1000
        check("dance step cost << 20 ms", ms < 10.0, f"{ms:.2f} ms per 50 Hz step (policy_ufo.onnx)")

    # ---------------------------------------------------------------- model.json
    t = time.perf_counter()
    model, ids, ntris = model_export.load_or_build(rs.ROBOT_XML, lambda: rs.load_model(visual=True),
                                                   os.path.join(HERE, "model_cache.json"))
    names = [b["name"] for b in model["bodies"]]
    idx_ok = all(max(m["tris"]) < len(m["verts"]) // 3 for b in model["bodies"] for m in b["meshes"])
    check("model.json: 25 bodies, <= 20k tris, valid indices", len(names) == 25 and 0 < ntris <= 20000 and idx_ok,
          f"{len(names)} bodies, {ntris} tris ({time.perf_counter() - t:.1f}s)")

    n_fail = sum(1 for r in results if not r[1])
    print(f"SUMMARY: {len(results) - n_fail}/{len(results)} passed -> {'PASS' if n_fail == 0 else 'FAIL'}  "
          f"(sim {sim.t:.0f} s in {time.perf_counter() - t_wall:.0f} s wall, total falls={sim.falls}, resets={sim.resets})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
