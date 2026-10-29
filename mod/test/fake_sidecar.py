#!/usr/bin/env python3
"""Tiny stand-in for the rp1-sim sidecar (sidecar/CONTRACT.md API) used only by the mod's test harness.

Serves a small fake humanoid made of boxes (bodies named like the real RP1 MJCF, 'base_link' first), a pose that
yaws slowly and swings its legs, a `wave` counter that increments on every POST /cmd {"cmd":"wave"} (the right arm
is raised for 2 s), and records the last POST /cmd and /player bodies (GET /_last).
Usage: python3 fake_sidecar.py [port]   (default 8799, so it never collides with the real sidecar on 8797)
"""
import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
T0 = time.time()
LAST = {"cmd": None, "player": None, "player_count": 0}
STATE = {"mode": "stand", "wave": 0, "wave_until": 0.0}


def box(cx, cy, cz, hx, hy, hz):
    v = []
    for z in (-hz, hz):
        for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
            v += [cx + x, cy + y, cz + z]
    t = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]
    return v, t


def mesh(rgba, *boxes):
    verts, tris = [], []
    for b in boxes:
        v, t = box(*b)
        off = len(verts) // 3
        verts += v
        tris += [i + off for i in t]
    return {"rgba": rgba, "verts": [round(x, 4) for x in verts], "tris": tris}


WHITE, GREY, DARK = [0.92, 0.92, 0.92, 1], [0.55, 0.57, 0.6, 1], [0.15, 0.15, 0.17, 1]
BODIES = [
    # pelvis + torso + head on the floating base
    ("base_link", [mesh(WHITE, (0, 0, 0, 0.1, 0.15, 0.06), (0, 0, 0.25, 0.09, 0.16, 0.18)),
                   mesh(DARK, (0, 0, 0.55, 0.08, 0.07, 0.09))]),
    ("left_hip_pitch_link", [mesh(GREY, (0, 0, -0.2, 0.05, 0.05, 0.2))]),
    ("left_knee_link", [mesh(WHITE, (0, 0, -0.2, 0.045, 0.045, 0.2))]),
    ("left_ankle_roll_link", [mesh(DARK, (0.04, 0, -0.03, 0.11, 0.05, 0.03))]),
    ("right_hip_pitch_link", [mesh(GREY, (0, 0, -0.2, 0.05, 0.05, 0.2))]),
    ("right_knee_link", [mesh(WHITE, (0, 0, -0.2, 0.045, 0.045, 0.2))]),
    ("right_ankle_roll_link", [mesh(DARK, (0.04, 0, -0.03, 0.11, 0.05, 0.03))]),
    ("left_shoulder_pitch_link", [mesh(GREY, (0, 0, -0.14, 0.04, 0.04, 0.14))]),
    ("left_elbow_link", [mesh(WHITE, (0, 0, -0.13, 0.035, 0.035, 0.13))]),
    ("right_shoulder_pitch_link", [mesh(GREY, (0, 0, -0.14, 0.04, 0.04, 0.14))]),
    ("right_elbow_link", [mesh(WHITE, (0, 0, -0.13, 0.035, 0.035, 0.13))]),
]
MODEL = {"version": 1, "bodies": [{"name": n, "meshes": m} for n, m in BODIES]}


def qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return [w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2]


def rot(q, v):
    w, x, y, z = q
    p = qmul(qmul(q, [0] + list(v)), [w, -x, -y, -z])
    return p[1:]


def add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def axq(axis, ang):
    s = math.sin(ang / 2)
    return [math.cos(ang / 2), axis[0] * s, axis[1] * s, axis[2] * s]


def pose():
    now = time.time()
    t = now - T0
    yaw = 0.3 * t
    qb = axq((0, 0, 1), yaw)
    pb = [0.0, 0.0, 0.95]
    out = {"base_link": pb + qb}
    swing = 0.4 * math.sin(2 * t) if STATE["mode"] == "walk" else 0.1 * math.sin(t)
    for side, sy, sg in (("left", 0.1, 1), ("right", -0.1, -1)):
        qh = qmul(qb, axq((0, 1, 0), sg * swing))
        ph = add(pb, rot(qb, (0, sy, -0.06)))
        qk = qmul(qh, axq((0, 1, 0), 0.2))
        pk = add(ph, rot(qh, (0, 0, -0.4)))
        pa = add(pk, rot(qk, (0, 0, -0.4)))
        out[side + "_hip_pitch_link"] = ph + qh
        out[side + "_knee_link"] = pk + qk
        out[side + "_ankle_roll_link"] = pa + qb
    waving = now < STATE["wave_until"]
    for side, sy, sg in (("left", 0.21, 1), ("right", -0.21, -1)):
        lift = -2.6 + 0.4 * math.sin(8 * t) if (waving and side == "right") else 0.0
        qs = qmul(qb, axq((1, 0, 0), sg * -lift))
        qs = qmul(qs, axq((0, 1, 0), -sg * swing))
        ps = add(pb, rot(qb, (0, sy, 0.4)))
        qe = qmul(qs, axq((0, 1, 0), -0.3))
        pe = add(ps, rot(qs, (0, 0, -0.28)))
        out[side + "_shoulder_pitch_link"] = ps + qs
        out[side + "_elbow_link"] = pe + qe
    mode = "wave" if waving else STATE["mode"]
    return {"seq": int(t * 50), "t": round(t, 3), "mode": mode, "fallen": False, "wave": STATE["wave"],
            "bodies": {k: [round(x, 5) for x in v] for k, v in out.items()}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._send({"ok": True, "mode": STATE["mode"], "fallen": False, "t": time.time() - T0,
                        "policy": "fake", "hz": 50})
        elif self.path == "/model.json":
            self._send(MODEL)
        elif self.path == "/pose":
            self._send(pose())
        elif self.path == "/_last":
            self._send(LAST)
        else:
            self._send({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/cmd":
            LAST["cmd"] = body
            c = body.get("cmd")
            if c == "reset" and str(body.get("from", "")).startswith("twitch:"):
                self._send({"ok": False, "error": "forbidden"}, 403)
            elif c == "wave":
                STATE["wave"] += 1
                STATE["wave_until"] = time.time() + 2.0
                self._send({"ok": True, "mode": "wave"})
            elif c in ("walk", "stop", "follow", "turn", "reset", "dance"):
                STATE["mode"] = {"stop": "stand", "reset": "stand"}.get(c, c)
                self._send({"ok": True, "mode": STATE["mode"]})
            else:
                self._send({"ok": False, "error": "unknown cmd"})
        elif self.path == "/player":
            LAST["player"] = body
            LAST["player_count"] += 1
            self._send({"ok": True})
        else:
            self._send({"ok": False, "error": "not found"}, 404)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
