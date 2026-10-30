#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-only: see sidecar/LICENSE).
"""RP1 MuJoCo sim sidecar - HTTP API on 127.0.0.1:8797 (see CONTRACT.md)."""

import argparse
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("OMP_NUM_THREADS", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mujoco  # noqa: E402

import rp1_sim as rs  # noqa: E402
import model_export  # noqa: E402

HOST = "127.0.0.1"


LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def foreign_request(headers):
    """Why a request must be refused, or None. Only local programs (the mod, the tools) may drive the
    sim: a web page in any browser on this machine, including the in-game web walls, can also reach
    127.0.0.1. Browsers always send Origin on cross-site POSTs, and a DNS-rebinding page carries its
    own host name in Host; the mod and the Python tools send neither."""
    if headers.get("Origin"):
        return "requests from web pages are refused"
    host = (headers.get("Host") or "").strip()
    name = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
    if name.lower() not in LOOPBACK_NAMES:
        return "only 127.0.0.1 / localhost may be used as the Host"
    return None
PORT = int(os.environ.get("RP1_PORT", "8797"))
TWITCH_ALLOWED = {"walk", "stop", "stand", "follow", "turn", "wave", "dance", "home"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Service:
    def __init__(self):
        self.sim = rs.RP1Sim(log=log)
        t = time.time()
        model, ids, ntris = model_export.load_or_build(rs.ROBOT_XML, lambda: rs.load_model(visual=True),
                                                       os.path.join(HERE, "model_cache.json"))
        self.model_bytes = json.dumps(model, separators=(",", ":")).encode()
        self.body_ids = ids
        self.body_names = [b["name"] for b in model["bodies"]]
        # resolve ids by name in the physics model (it has no visual geoms, but the same bodies)
        self.body_ids = [mujoco.mj_name2id(self.sim.model, mujoco.mjtObj.mjOBJ_BODY, n) for n in self.body_names]
        assert min(self.body_ids) >= 0, "model.json body missing from the physics model"
        self.ntris = ntris
        log(f"model.json: {len(self.body_names)} bodies, {ntris} tris, {len(self.model_bytes)} bytes ({time.time() - t:.1f}s)")
        self.lock = self.sim.lock
        self.snap_lock = threading.Lock()
        self.seq = 0
        self.pose_bytes = b"{}"
        self.health = {}
        self.overruns = 0
        self.step_ms = 0.0
        self.stop = threading.Event()
        self._snapshot()

    # -- called with self.lock held
    def _snapshot(self):
        s = self.sim
        d = s.data
        bodies = {}
        for name, b in zip(self.body_names, self.body_ids):
            p = d.xpos[b]
            q = d.xquat[b]
            bodies[name] = [round(float(p[0]), 5), round(float(p[1]), 5), round(float(p[2]), 5),
                            round(float(q[0]), 5), round(float(q[1]), 5), round(float(q[2]), 5), round(float(q[3]), 5)]
        self.seq += 1
        mode = s.public_mode()
        pose = {"seq": self.seq, "t": round(s.t, 3), "mode": mode, "fallen": bool(s.fallen),
                "wave": s.waves, "bodies": bodies}
        health = {"ok": True, "mode": mode, "fallen": bool(s.fallen), "t": round(s.t, 3),
                  "policy": s.policies[s.active].file, "hz": 50, "leash": s.leash, "wave": s.waves,
                  "resets": s.resets, "falls": s.falls, "seq": self.seq, "overruns": self.overruns,
                  "step_ms": round(self.step_ms, 2), "player": s.player,
                  "pos": [round(float(x), 3) for x in s.pos()], "yaw": round(math.degrees(s.yaw()), 1),
                  "cmd_vel": [round(float(x), 3) for x in s.cmd],
                  "talk": bool(s.talk_on), "talking": round(float(s.talk_env), 2), "talks": s.talks,
                  "extras": sorted(k for k in ("wave", "dance") if k in s.policies)}
        b = json.dumps(pose, separators=(",", ":")).encode()
        with self.snap_lock:
            self.pose_bytes = b
            self.health = health

    def run(self):
        dt = rs.CONTROL_DT
        nxt = time.monotonic()
        while not self.stop.is_set():
            t0 = time.perf_counter()
            with self.lock:
                self.sim.step()
                self._snapshot()
            self.step_ms = 0.98 * self.step_ms + 0.02 * (time.perf_counter() - t0) * 1000
            nxt += dt
            now = time.monotonic()
            delay = nxt - now
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:  # fell far behind (suspend/CPU starvation): resync, don't spiral
                self.overruns += 1
                nxt = now

    def get_pose(self):
        with self.snap_lock:
            return self.pose_bytes

    def get_health(self):
        with self.snap_lock:
            return dict(self.health)


SERVICE = None


class Handler(BaseHTTPRequestHandler):
    server_version = "rp1-sim/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the journal quiet (pose is polled at 20+ Hz)
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        obj = json.loads(raw or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    def do_GET(self):
        why = foreign_request(self.headers)
        if why:
            return self._send(403, {"ok": False, "error": why})
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._send(200, SERVICE.get_health())
        if path == "/pose":
            return self._send(200, SERVICE.get_pose())
        if path == "/model.json":
            return self._send(200, SERVICE.model_bytes)
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        why = foreign_request(self.headers)
        if why:
            return self._send(403, {"ok": False, "error": why})
        path = self.path.split("?", 1)[0]
        try:
            body = self._json_body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad json: {e}"})
        sim = SERVICE.sim
        if path == "/cmd":
            cmd = str(body.get("cmd", "")).strip().lower()
            src = str(body.get("from", "?"))
            if cmd in ("left", "right"):  # chat aliases
                body["dir"] = cmd
                cmd = "turn"
            if src.startswith("twitch:") and cmd not in TWITCH_ALLOWED:
                return self._send(403, {"ok": False, "error": f"cmd {cmd!r} not allowed from twitch"})
            kw = {}
            try:
                for k in ("vx", "vy", "wz"):
                    if k in body and body[k] is not None:
                        v = float(body[k])
                        if not math.isfinite(v):
                            raise ValueError(k)
                        kw[k] = v
            except (TypeError, ValueError):
                return self._send(400, {"ok": False, "error": "vx/vy/wz must be numbers"})
            if "dir" in body:
                kw["dir"] = str(body["dir"]).lower()
            if cmd == "talk":
                kw["on"] = body.get("on", True)
                if body.get("level") is not None:
                    try:
                        kw["level"] = float(body["level"])
                        if not math.isfinite(kw["level"]):
                            raise ValueError
                    except (TypeError, ValueError):
                        return self._send(400, {"ok": False, "error": "level must be a number"})
            if cmd == "turn" and kw.get("dir") not in ("left", "right"):
                return self._send(400, {"ok": False, "error": "turn needs \"dir\":\"left\"|\"right\""})
            with SERVICE.lock:
                ok, err = sim.command(cmd, **kw)
                mode = sim.public_mode()
            if cmd != "talk" or "level" not in kw:  # ~10 Hz talk level updates would flood the journal
                log(f"cmd {cmd} {kw if kw else ''} from={src} -> ok={ok} mode={mode}{' err=' + err if err else ''}")
            if ok:
                return self._send(200, {"ok": True, "mode": mode})
            return self._send(400, {"ok": False, "error": err})
        if path == "/player":
            try:
                x, y = float(body["x"]), float(body["y"])
                if not (math.isfinite(x) and math.isfinite(y)):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                return self._send(400, {"ok": False, "error": "need numeric x and y"})
            with SERVICE.lock:
                sim.set_player(x, y)
            return self._send(200, {"ok": True})
        if path == "/config":
            if "leash" in body:
                try:
                    r = float(body["leash"])
                    if not (math.isfinite(r) and 0.5 <= r <= 100):
                        raise ValueError
                except (TypeError, ValueError):
                    return self._send(400, {"ok": False, "error": "leash must be a number in [0.5, 100]"})
                with SERVICE.lock:
                    sim.leash = r
                log(f"config leash={r}")
            return self._send(200, {"ok": True, "leash": sim.leash})
        return self._send(404, {"ok": False, "error": "not found"})


def main():
    global SERVICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    SERVICE = Service()
    th = threading.Thread(target=SERVICE.run, name="sim", daemon=True)
    th.start()
    tries = 0
    while True:  # don't crash-loop if the port is taken by something else: wait for it (sim keeps running)
        try:
            httpd = ThreadingHTTPServer((HOST, args.port), Handler)
            break
        except OSError as e:
            if tries % 30 == 0:  # log on the first failure, then every 5 min
                log(f"cannot bind {HOST}:{args.port} ({e}); retrying every 10 s")
            tries += 1
            time.sleep(10)
    httpd.daemon_threads = True
    log(f"listening on http://{HOST}:{args.port}  policies={[p.file for p in SERVICE.sim.policies.values()]} "
        f"gains={SERVICE.sim.gains}")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        SERVICE.stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
