#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-only: see sidecar/LICENSE).
"""HTTP test against the running sidecar (default http://127.0.0.1:8797). Exercises the whole API, incl. a real walk,
turn, follow, wave and reset, checking the robot actually moves and stays up."""

import json
import math
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8797"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def cmd(c, **kw):
    body = {"cmd": c, "from": "mc"}
    body.update(kw)
    return post("/cmd", body)


def base_xy():
    _, p = get("/pose")
    b = p["bodies"]["base_link"]
    return b[0], b[1], p


def yaw_of(b):
    w, x, y, z = b[3:7]
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def main():
    st, h = get("/health")
    check("GET /health", st == 200 and h.get("ok") is True and h.get("hz") == 50 and "mode" in h and "policy" in h,
          json.dumps({k: h[k] for k in ("ok", "mode", "fallen", "t", "policy", "hz")}))
    falls0 = h["falls"]

    t0 = time.time()
    st, m = get("/model.json")
    names = [b["name"] for b in m["bodies"]]
    ntris = sum(len(me["tris"]) // 3 for b in m["bodies"] for me in b["meshes"])
    idx_ok = all(max(me["tris"]) < len(me["verts"]) // 3 for b in m["bodies"] for me in b["meshes"])
    rgba_ok = all(len(me["rgba"]) == 4 for b in m["bodies"] for me in b["meshes"])
    check("GET /model.json", st == 200 and m.get("version") == 1 and 0 < ntris <= 20000 and idx_ok and rgba_ok,
          f"{len(names)} bodies, {ntris} tris, fetched in {time.time() - t0:.2f}s")
    print("      bodies:", ", ".join(names))

    st, p1 = get("/pose")
    time.sleep(1.0)
    st2, p2 = get("/pose")
    dseq = p2["seq"] - p1["seq"]
    dt = p2["t"] - p1["t"]
    check("GET /pose has every model body", set(p1["bodies"]) == set(names) and all(len(v) == 7 for v in p1["bodies"].values())
          and "wave" in p1, f"{len(p1['bodies'])} bodies, base_link={p1['bodies'].get('base_link')}")
    check("pose seq advances in real time (~50/s)", 45 <= dseq <= 55 and 0.9 <= dt <= 1.1,
          f"seq +{dseq} and sim t +{dt:.2f}s over 1.0 s wall")

    st, r = cmd("reset")
    check("POST /cmd reset", st == 200 and r.get("mode") == "stand", json.dumps(r))
    time.sleep(1.0)

    x0, y0, _ = base_xy()
    st, r = cmd("walk")
    check("POST /cmd walk", st == 200 and r.get("mode") == "walk", json.dumps(r))
    time.sleep(6.0)
    x1, y1, p = base_xy()
    check("walk moves forward (~0.4 m/s)", x1 - x0 > 1.4 and not p["fallen"], f"dx={x1 - x0:.2f} m in 6 s (incl. start-up), dy={y1 - y0:+.2f}")
    st, r = post("/cmd", {"cmd": "stop", "from": "twitch:tester"})
    check("POST /cmd stop (twitch)", st == 200 and r.get("mode") == "stand", json.dumps(r))
    time.sleep(2.0)

    _, _, p = base_xy()
    yaw0 = yaw_of(p["bodies"]["base_link"])
    st, r = cmd("left")
    check("POST /cmd left (alias)", st == 200 and r.get("mode") == "turn", json.dumps(r))
    time.sleep(4.0)
    _, h = get("/health")
    _, _, p = base_xy()
    dy = math.degrees(math.atan2(math.sin(yaw_of(p["bodies"]["base_link"]) - yaw0), math.cos(yaw_of(p["bodies"]["base_link"]) - yaw0)))
    check("turn left ~90 deg then stand", 70 < dy < 110 and h["mode"] == "stand", f"dyaw={dy:+.1f} deg, mode={h['mode']}")
    st, r = post("/cmd", {"cmd": "turn", "dir": "right", "from": "twitch:tester"})
    check("POST /cmd turn right (twitch)", st == 200 and r.get("mode") == "turn", json.dumps(r))
    time.sleep(4.0)

    st, r = post("/player", {"x": -1.5, "y": 1.5})
    check("POST /player", st == 200 and r.get("ok"), json.dumps(r))
    st, r = cmd("follow")
    check("POST /cmd follow", st == 200 and r.get("mode") == "follow", json.dumps(r))
    time.sleep(12.0)
    x, y, p = base_xy()
    d = math.hypot(x + 1.5, y - 1.5)
    check("follow approaches player, stops ~1 m away", 0.5 < d < 1.5 and not p["fallen"], f"dist={d:.2f} m")

    st, r = post("/config", {"leash": 4.0})
    check("POST /config leash", st == 200 and r.get("leash") == 4.0, json.dumps(r))
    post("/config", {"leash": 6.0})

    _, p = get("/pose")
    w0 = p["wave"]
    st, r = cmd("wave")
    check("POST /cmd wave", st == 200 and r.get("mode") == "wave", json.dumps(r))
    time.sleep(1.3)
    _, p = get("/pose")
    hand = p["bodies"]["right_wrist_link"][2]
    head = p["bodies"]["waist_yaw_link"][2]
    check("wave raises the right arm", hand > head + 0.3 and p["wave"] == w0 + 1, f"right wrist z={hand:.2f} vs torso z={head:.2f}, wave counter {w0}->{p['wave']}")
    time.sleep(5.0)
    _, h = get("/health")
    check("wave ends -> stand on policy.onnx", h["mode"] == "stand" and h["policy"] == "policy.onnx" and not h["fallen"],
          json.dumps({k: h[k] for k in ("mode", "policy", "fallen")}))

    st, r = post("/cmd", {"cmd": "reset", "from": "twitch:tester"})
    check("twitch cannot reset", st == 403 and r.get("ok") is False, json.dumps(r))
    st, r = cmd("fly")
    check("unknown cmd rejected", st == 400 and r.get("ok") is False, json.dumps(r))
    st, r = cmd("turn")
    check("turn without dir rejected", st == 400 and r.get("ok") is False, json.dumps(r))
    st, r = post("/player", {"x": "a"})
    check("bad /player rejected", st == 400, json.dumps(r))
    st, r = cmd("reset")
    time.sleep(1.0)
    x, y, p = base_xy()
    check("reset -> upright at origin", math.hypot(x, y) < 0.05 and not p["fallen"] and p["mode"] == "stand", f"base at ({x:.3f},{y:.3f})")
    _, h = get("/health")
    check("no falls during the tour", h["falls"] == falls0, f"falls +{h['falls'] - falls0}, resets={h['resets']} overruns={h['overruns']} step_ms={h['step_ms']}")
    n_fail = results.count(False)
    print(f"SUMMARY: {len(results) - n_fail}/{len(results)} passed -> {'PASS' if n_fail == 0 else 'FAIL'}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
