"""Render the README's sim images from the sidecar's own in-process simulation (no Minecraft, no HTTP).

Everything here is the real RP1 driven by RoboParty's real policies in MuJoCo: the same code path as
sidecar/render_preview.py, with a studio look (cream backdrop, a distance fade of the floor into it, a slanted key light).
Needs the upstream files: run sidecar/setup.sh (or sidecar/fetch_upstream.py) first. Run from the repo root:

    MUJOCO_GL=egl sidecar/.venv/bin/python docs/media/make_renders.py

Writes docs/media/sim-poses.png (stand / walk / wave / dance) and docs/media/sim-loop.webp (wave, then dance).
"""
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("OMP_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "sidecar"))

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import rp1_sim as rs  # noqa: E402

CREAM = np.array([245, 240, 232], np.float32)
FONTS = Path(os.environ.get("OMO_FONTS", HERE / "fonts"))


def studio(sim):
    m = sim.model
    floor = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "floor")
    m.geom_rgba[floor] = FLOOR
    # the scene's one light becomes a slanted key spot that follows the robot (see Cam.shot)
    m.light_type[0] = mujoco.mjtLightType.mjLIGHT_SPOT
    m.light_cutoff[0] = 60
    m.light_exponent[0] = 1
    m.light_diffuse[0] = [0.70, 0.68, 0.65]
    m.light_castshadow[0] = 1
    m.vis.headlight.ambient[:] = [0.36, 0.35, 0.34]
    m.vis.headlight.diffuse[:] = [0.30, 0.30, 0.30]
    m.vis.quality.shadowsize = 4096
    m.vis.quality.offsamples = 8


FLOOR = [1.0, 0.98, 0.94, 1]
FOG = (3.0, 7.0)
KEY = np.array([1.4, 1.2, 3.4])   # key light offset from the robot (sim metres)


class Cam:
    def __init__(self, sim, w, h):
        self.sim = sim
        self.r = mujoco.Renderer(sim.model, h, w)
        self.cam = mujoco.MjvCamera()
        self.cam.distance = 2.5
        self.cam.elevation = -8
        self.opt = mujoco.MjvOption()

    def shot(self, az, lookz=0.62):
        p = self.sim.pos()
        d = self.sim.data
        self.cam.lookat[:] = [p[0], p[1], lookz]
        self.cam.azimuth = az
        target = np.array([p[0], p[1], 0.4])
        d.light_xpos[0] = target + KEY
        d.light_xdir[0] = -KEY / np.linalg.norm(KEY)
        self.r.update_scene(d, self.cam, self.opt)
        self.r.scene.flags[mujoco.mjtRndFlag.mjRND_SKYBOX] = 0
        rgb = self.r.render().astype(np.float32)
        self.r.enable_depth_rendering()
        depth = self.r.render()
        self.r.disable_depth_rendering()
        # distance fade onto the backdrop colour (metres from the camera; the robot itself is ~2.5 m away),
        # so the floor melts into the backdrop with no horizon line
        t = np.clip((depth - FOG[0]) / (FOG[1] - FOG[0]), 0, 1)
        t = (t * t * (3 - 2 * t))[..., None]
        rgb = rgb * (1 - t) + CREAM * t
        return Image.fromarray(rgb.clip(0, 255).astype(np.uint8))


def run(sim, seconds):
    for _ in range(int(round(seconds / rs.CONTROL_DT))):
        sim.step()


def poses():
    sim = rs.RP1Sim(log=lambda *a: None, visual=True)
    studio(sim)
    cam = Cam(sim, 520, 720)
    tiles = []
    run(sim, 1.0)
    tiles.append(("stand", cam.shot(150)))
    sim.command("walk")
    run(sim, 3.06)
    tiles.append(("walk", cam.shot(100)))
    sim.command("reset")
    run(sim, 1.0)
    sim.command("wave")
    run(sim, 1.3)
    tiles.append(("wave", cam.shot(165)))
    sim.command("reset")
    run(sim, 1.0)
    sim.command("dance")
    run(sim, 9.0)
    tiles.append(("dance", cam.shot(140)))
    gap = 18
    w, h = tiles[0][1].size
    sheet = Image.new("RGB", (4 * w + 3 * gap, h), tuple(int(c) for c in CREAM))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(str(FONTS / "IBMPlexMono-Medium.ttf"), 26)
    except OSError:
        f = ImageFont.load_default()
    for i, (name, im) in enumerate(tiles):
        x = i * (w + gap)
        sheet.paste(im, (x, 0))
        label = f"!rp1 {name}"
        tw = d.textlength(label, font=f)
        d.rounded_rectangle((x + 20, h - 70, x + 20 + tw + 36, h - 24), radius=22, fill=(26, 16, 8))
        d.text((x + 38, h - 47), label, font=f, fill=(245, 240, 232), anchor="lm")
    out = HERE / "sim-poses.png"
    sheet.save(out, optimize=True)
    print("wrote", out, sheet.size)


def loop(fps=20):
    sim = rs.RP1Sim(log=lambda *a: None, visual=True)
    studio(sim)
    cam = Cam(sim, 560, 640)
    per = int(round(1 / (fps * rs.CONTROL_DT)))  # policy steps per frame
    frames = []
    run(sim, 0.6)
    sim.command("wave")
    for k in range(int(4.6 * fps)):
        run(sim, per * rs.CONTROL_DT)
        frames.append(cam.shot(165 + 10 * math.sin(k / 40)))
    sim.command("dance")
    for k in range(int(9.0 * fps)):
        run(sim, per * rs.CONTROL_DT)
        frames.append(cam.shot(150 + 25 * math.sin(k / 60)))
    out = HERE / "sim-loop.webp"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0,
                   quality=72, method=6)
    print("wrote", out, len(frames), "frames", os.path.getsize(out), "bytes")


if __name__ == "__main__":
    what = sys.argv[1:] or ["poses", "loop"]
    if "poses" in what:
        poses()
    if "loop" in what:
        loop()
