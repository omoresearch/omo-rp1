# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-or-later: see sidecar/LICENSE).
"""Build /model.json: body-local, decimated visual meshes with the MJCF material colours.

version 1 fields (always present): per mesh "rgba", "verts", "tris".
Optional v1-compatible extras (same as microduck-sim, ignored by clients that don't use them):
  "name"    : MJCF mesh name
  "normals" : per-vertex unit normals (body-local), smoothed within CREASE_DEG, vertices split along creases
  "colors"  : per-vertex sRGB bytes = MJCF rgba x baked same-body ambient occlusion
  "cull"    : true when the decimated mesh is closed and consistently wound (back-face culling safe)

Decimation: vertex clustering with a binary-searched grid cell per mesh (quadric simplification stalls on these
CAD STLs). The triangle budget is split across meshes by surface area.
"""

import json
import os
import zlib

import mujoco
import numpy as np

EXPORT_VERSION = 1
TRI_BUDGET = 19000
MIN_TRIS = 60
CREASE_DEG = 40.0
AO_RAYS = 16
AO_RANGE = 0.05           # metres (RP1 is ~1.2 m tall)
AO_STRENGTH = 0.45


def _quat_to_mat(q):
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, np.asarray(q, dtype=np.float64))
    return m.reshape(3, 3)


def _area(v, f):
    a = v[f[:, 1]] - v[f[:, 0]]
    b = v[f[:, 2]] - v[f[:, 0]]
    return float(0.5 * np.linalg.norm(np.cross(a, b), axis=1).sum())


def _weld(v, f, tol=1e-7):
    key = np.round(v / tol).astype(np.int64)
    _, idx, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    f2 = inv.reshape(-1)[f]
    good = (f2[:, 0] != f2[:, 1]) & (f2[:, 1] != f2[:, 2]) & (f2[:, 0] != f2[:, 2])
    return v[idx], f2[good]


def _compact(v, f):
    used = np.unique(f)
    remap = -np.ones(len(v), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return v[used], remap[f]


def _cluster(v, f, cell):
    """Vertex-clustering decimation: snap to a grid of `cell` metres, average each cell."""
    key = np.floor(v / cell).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    n = inv.max() + 1
    nv = np.zeros((n, 3))
    np.add.at(nv, inv, v)
    cnt = np.bincount(inv, minlength=n)[:, None]
    nv /= cnt
    f2 = inv[f]
    good = (f2[:, 0] != f2[:, 1]) & (f2[:, 1] != f2[:, 2]) & (f2[:, 0] != f2[:, 2])
    f2 = f2[good]
    if len(f2):
        s = np.sort(f2, axis=1)
        _, keep = np.unique(s, axis=0, return_index=True)
        f2 = f2[np.sort(keep)]
    return _compact(nv, f2)


def _cluster_search(v, f, target):
    ext = float(np.max(v.max(0) - v.min(0))) or 1e-3
    lo, hi = ext / 3000, ext / 2
    best = None
    for _ in range(20):
        mid = (lo * hi) ** 0.5
        cv, cf = _cluster(v, f, mid)
        if len(cf) > target:
            lo = mid
        else:
            hi = mid
            best = (cv, cf)
    if best is None or len(best[1]) == 0:
        best = _cluster(v, f, lo)
    return best


def _decimate(v, f, target):
    v, f = _weld(v, f)
    if len(f) <= target:
        return _compact(v, f)
    return _cluster_search(v, f, target)


def _closed(f):
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    _, dcnt = np.unique(e, axis=0, return_counts=True)
    _, ucnt = np.unique(np.sort(e, axis=1), axis=0, return_counts=True)
    return bool((dcnt == 1).all() and (ucnt == 2).all())


def _smooth(v, f, crease_deg=CREASE_DEG):
    """Split vertices along creases; return (verts, normals, tris) with angle-weighted smooth normals."""
    p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    fn = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-20)[:, None]

    def ang(a, b, c):
        u, w = b - a, c - a
        cu = np.einsum("ij,ij->i", u, w) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(w, axis=1), 1e-20)
        return np.arccos(np.clip(cu, -1, 1))
    cang = np.stack([ang(p0, p1, p2), ang(p1, p2, p0), ang(p2, p0, p1)], 1).reshape(-1)
    cosc = np.cos(np.radians(crease_deg))
    nt = len(f)
    cv = f.reshape(-1)
    ct = np.repeat(np.arange(nt), 3)
    order = np.argsort(cv, kind="stable")
    sv = cv[order]
    starts = np.flatnonzero(np.r_[True, sv[1:] != sv[:-1]])
    ends = np.r_[starts[1:], len(sv)]
    cn = np.zeros((nt * 3, 3))
    for s, e in zip(starts, ends):
        cs = order[s:e]
        nrm = fn[ct[cs]]
        blend = nrm @ nrm.T >= cosc
        cn[cs] = (blend * cang[cs][None, :]) @ nrm
    cn /= np.maximum(np.linalg.norm(cn, axis=1), 1e-20)[:, None]
    key = np.concatenate([cv[:, None], np.round(cn * 2000).astype(np.int64)], axis=1)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    nv = v[cv[first]]
    nn = np.zeros((len(first), 3))
    np.add.at(nn, inv, cn)
    nn /= np.maximum(np.linalg.norm(nn, axis=1), 1e-20)[:, None]
    return nv, nn, inv.reshape(-1, 3)


def _fib_dirs(n):
    i = np.arange(n) + 0.5
    r = np.sqrt(i / n)
    phi = i * np.pi * (3 - np.sqrt(5))
    return np.stack([r * np.cos(phi), r * np.sin(phi), np.sqrt(1 - r * r)], 1)


def _ao(model, data, body, pw, nw):
    """Per-vertex AO from same-body visual geometry only (pose-invariant). Returns visibility 0..1."""
    dirs = _fib_dirs(AO_RAYS)
    gg = np.array([0, 0, 1, 0, 0, 0], np.uint8)
    gid = np.zeros(AO_RAYS, np.int32)
    dist = np.zeros(AO_RAYS)
    out = np.ones(len(pw))
    for i in range(len(pw)):
        n = nw[i]
        a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
        t = np.cross(n, a)
        t /= np.linalg.norm(t)
        b = np.cross(n, t)
        d = dirs @ np.stack([t, b, n])
        mujoco.mj_multiRay(model, data, pw[i] + n * 1e-3, d.reshape(-1).copy(), gg, 1, -1, gid, dist, None,
                           AO_RAYS, AO_RANGE)
        hit = (gid >= 0) & (dist >= 0) & (dist < AO_RANGE)
        hit &= model.geom_bodyid[np.maximum(gid, 0)] == body
        out[i] = 1 - np.where(hit, 1 - dist / AO_RANGE, 0.0).mean()
    return out


def body_name(model, b):
    n = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b)
    return n if n else f"body_{b}"


def _visual_geoms(model):
    for g in range(model.ngeom):
        if model.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH or model.geom_bodyid[g] == 0:
            continue
        if model.geom_group[g] != 2 or (model.geom_contype[g] or model.geom_conaffinity[g]):
            continue
        yield g


def build_model_json(model, budget=TRI_BUDGET, ao=True):
    """Return (dict, body_ids, ntris) for every body with at least one visual mesh geom."""
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    geoms = list(_visual_geoms(model))
    # split the budget by surface area (per unique mesh), with a floor
    areas = {}
    for g in geoms:
        mid = int(model.geom_dataid[g])
        if mid in areas:
            continue
        va, vn = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
        fa, fn = model.mesh_faceadr[mid], model.mesh_facenum[mid]
        areas[mid] = _area(model.mesh_vert[va:va + vn].astype(np.float64), model.mesh_face[fa:fa + fn])
    tot = sum(areas.values()) or 1.0
    spare = budget - MIN_TRIS * len(areas)
    targets = {mid: int(MIN_TRIS + spare * a / tot) for mid, a in areas.items()}
    bodies = {}
    cache = {}
    for g in geoms:
        mid = int(model.geom_dataid[g])
        mname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, mid) or f"mesh_{mid}"
        b = int(model.geom_bodyid[g])
        if model.geom_matid[g] >= 0:
            rgba = np.array(model.mat_rgba[model.geom_matid[g]], dtype=float)
        else:
            rgba = np.array(model.geom_rgba[g], dtype=float)
        if mid not in cache:
            va, vn = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
            fa, fn = model.mesh_faceadr[mid], model.mesh_facenum[mid]
            v = model.mesh_vert[va:va + vn].astype(np.float64)
            f = model.mesh_face[fa:fa + fn].astype(np.int64)
            dv, df = _decimate(v, f, targets[mid])
            closed = _closed(df)
            sv, sn, sf = _smooth(dv, df)
            cache[mid] = (sv, sn, sf, closed)
        sv, sn, sf, closed = cache[mid]
        R = _quat_to_mat(model.geom_quat[g])
        vb = sv @ R.T + model.geom_pos[g]           # body-local (MuJoCo recentres meshes into geom_pos/quat)
        nb = sn @ R.T
        if ao:
            Rb = data.xmat[b].reshape(3, 3)
            vis = _ao(model, data, b, vb @ Rb.T + data.xpos[b], nb @ Rb.T)
        else:
            vis = np.ones(len(vb))
        shade = 1 - AO_STRENGTH * (1 - vis)
        cols = np.clip(np.round(rgba[:3][None, :] * shade[:, None] * 255), 0, 255).astype(np.int64)
        bodies.setdefault(b, []).append({
            "name": mname,
            "rgba": [round(float(c), 4) for c in rgba],
            "verts": [round(float(x), 5) for x in vb.reshape(-1)],
            "tris": [int(i) for i in sf.reshape(-1)],
            "normals": [round(float(x), 3) for x in nb.reshape(-1)],
            "colors": [int(c) for c in cols.reshape(-1)],
            "cull": closed,
        })
    ntris = sum(len(m["tris"]) // 3 for ms in bodies.values() for m in ms)
    body_ids = sorted(bodies)
    out = {"version": 1, "bodies": [{"name": body_name(model, b), "meshes": bodies[b]} for b in body_ids]}
    return out, body_ids, ntris


def _sig(xml_path):
    """Signature of the export parameters + source files (MJCF text and mesh file sizes)."""
    with open(xml_path, "rb") as fh:
        xml = fh.read()
    mdir = os.path.join(os.path.dirname(xml_path), "..", "meshes")
    sizes = ",".join(f"{n}:{os.path.getsize(os.path.join(mdir, n))}" for n in sorted(os.listdir(mdir))
                     if n.lower().endswith(".stl"))
    return (f"v{EXPORT_VERSION}-{TRI_BUDGET}-{MIN_TRIS}-{CREASE_DEG}-{AO_RAYS}-{AO_RANGE}-{AO_STRENGTH}"
            f"-{zlib.crc32(xml):08x}-{zlib.crc32(sizes.encode()):08x}")


def load_or_build(xml_path, load_visual_model, cache_path):
    """Cache the JSON on disk (decimation + baked AO take ~40 s). Delete the cache file to rebuild.
    load_visual_model(): returns the MjModel *with* visual mesh geoms (only called on a cache miss)."""
    src_sig = _sig(xml_path)
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as fh:
                blob = json.load(fh)
            if blob.get("_sig") == src_sig:
                return blob["model"], blob["_body_ids"], blob["_ntris"]
        except Exception:
            pass
    out, ids, ntris = build_model_json(load_visual_model())
    with open(cache_path + ".tmp", "w") as fh:
        json.dump({"_sig": src_sig, "_body_ids": ids, "_ntris": ntris, "model": out}, fh, separators=(",", ":"))
    os.replace(cache_path + ".tmp", cache_path)
    return out, ids, ntris


if __name__ == "__main__":
    import sys
    import time
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rp1_sim
    t = time.time()
    m = rp1_sim.load_model(visual=True)
    out, ids, n = build_model_json(m)
    print(f"{len(ids)} bodies, {n} tris, {len(json.dumps(out, separators=(',', ':')))} bytes, {time.time() - t:.1f}s")
    for b in out["bodies"]:
        print(f"  {b['name']:28s} {sum(len(x['tris']) // 3 for x in b['meshes']):6d} tris cull={[x['cull'] for x in b['meshes']]}")
