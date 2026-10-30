#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-only: see sidecar/LICENSE).
"""Fetch only the needed RoboParty files into ./upstream/, at pinned commits. Idempotent.

Nothing fetched here is redistributed by this repository: it is downloaded on your machine,
straight from RoboParty's own GitHub repositories.

  RP1_description      robot MJCF + body STL meshes        (no licence stated upstream)
  roboparty_inference  RP1 24-DoF ONNX policies + configs  (GPL-3.0) and the obs/deploy reference sources
  robolab              actuator + env configs the policies were trained with (BSD-3-Clause)

Pins can be overridden, e.g. RP1_REV_RP1_DESCRIPTION=master ./fetch_upstream.py (untested combinations).
Set GITHUB_TOKEN to lift the anonymous GitHub API rate limit (one API call per run is needed).
"""
import hashlib
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
ORG = "Roboparty"
# The commits this sidecar was developed and tested against (2026-10-27).
PINS = {
    "RP1_description": "d8c6dd03aeecc7f7ead3d962e09fedba4c8460f0",
    "roboparty_inference": "7a1178256d2b0f0e27427248076cfadc4db12f4d",
    "robolab": "9ccf7f2c568b5b462c880dd2b1a6ac72fd6895a0",
}
UA = {"User-Agent": "omo-rp1-sidecar/1.0 (fetch_upstream.py)"}


def rev(repo):
    return os.environ.get("RP1_REV_" + repo.upper(), PINS[repo])


def get(url, timeout=120, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(repo, path):
    dst = os.path.join(UP, repo, path)
    if os.path.exists(dst) and os.path.getsize(dst) > 200:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    data = get(f"https://raw.githubusercontent.com/{ORG}/{repo}/{rev(repo)}/{path}")
    if data.startswith(b"version https://git-lfs"):  # Git LFS pointer -> the real file
        oid = re.search(rb"^oid sha256:([0-9a-f]{64})$", data, re.M)
        data = get(f"https://media.githubusercontent.com/media/{ORG}/{repo}/{rev(repo)}/{path}", timeout=300)
        # The pointer comes from the pinned commit, so its hash pins the large file too.
        if not oid or hashlib.sha256(data).hexdigest() != oid.group(1).decode():
            sys.exit(f"{repo}/{path}: downloaded file does not match the pinned commit's LFS hash; refusing")
    with open(dst + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(dst + ".tmp", dst)
    print(f"{len(data):>10} {repo}/{path}", flush=True)
    return dst


def tree(repo):
    hdr = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{ORG}/{repo}/git/trees/{rev(repo)}?recursive=1"
    data = json.loads(get(url, headers=hdr))
    return [t["path"] for t in data["tree"] if t["type"] == "blob"]


def main():
    # robot model: the 24-DoF MJCF, its floor scene and only the body meshes it references (no hands)
    for p in ("mjcf/robot_24dof.xml", "mjcf/rp1_flat.xml"):
        fetch("RP1_description", p)
    xml = open(os.path.join(UP, "RP1_description", "mjcf/robot_24dof.xml")).read()
    for m in sorted(set(re.findall(r'file="([^"]+\.(?:STL|stl))"', xml))):
        fetch("RP1_description", "meshes/" + os.path.basename(m))
    # policies + deploy configs + the deploy sources the observation builder mirrors
    for p in tree("roboparty_inference"):
        if p.startswith("robots/rp1_24dof/") or p in (
                "src/obs_manager.cpp", "src/inference_node.cpp", "src/inference_node.hpp",
                "src/utils/decouple_rp1.cpp", "src/utils/motion_loader.cpp", "src/utils/motion_loader.hpp",
                "src/utils/latent_loader.cpp", "src/utils/latent_loader.hpp"):
            fetch("roboparty_inference", p)
    # training reference (gains / actuator model the policies were trained with)
    for p in ("robolab/assets/robots/roboparty.py", "robolab/assets/robots/roboparty_actuators.py",
              "robolab/tasks/direct/base/rp1_env_cfg.py", "robolab/tasks/direct/base/base_config.py",
              "robolab/tasks/manager_based/beyondmimic/rp1_beyondmimic_env_cfg.py"):
        fetch("robolab", p)
    with open(os.path.join(UP, "REVISIONS.txt"), "w") as fh:
        for repo in PINS:
            fh.write(f"{ORG}/{repo} {rev(repo)}\n")
    print("done: upstream/ ready (" + ", ".join(f"{r}@{rev(r)[:7]}" for r in PINS) + ")", file=sys.stderr)


if __name__ == "__main__":
    main()
