"""Compose the README hero (light + dark) and the GitHub social preview from a real in-game frame.

The picture is ingame-lab.jpg: an unedited frame from the robot-lab loop in the omo-space README
(the RP1, simulated live in MuJoCo and drawn by this mod, next to a Microduck in the Omo Space white lab).
No webcam, chat or HUD is in the frame. This script only crops it, frames it and sets the type.
Fonts: Crimson Text + IBM Plex Mono (SIL OFL). Run from the repo root:
    python3 docs/media/make_hero.py path/to/fonts
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "fonts"

CREAM = (245, 240, 232)
INK = (26, 16, 8)
BROWN = (74, 59, 47)
LINE = (212, 205, 192)
SAND = (196, 184, 168)
ORANGE = (226, 104, 36)
THEMES = {
    "light": dict(bg=CREAM, fg=INK, sub=BROWN, pill_bg=INK, pill_fg=CREAM, frame=LINE),
    "dark": dict(bg=(18, 12, 7), fg=CREAM, sub=SAND, pill_bg=CREAM, pill_fg=INK, frame=BROWN),
}
CROP = (62, 0, 382, 448)   # the RP1 and the Microduck, portrait


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def fit(d, text, name, size, max_w):
    while size > 40 and d.textlength(text, font=font(name, size)) > max_w:
        size -= 2
    return font(name, size)


def plate(img, d, c, box, caption_size):
    """Paste the cropped real frame into box=(x, y, w, h) with rounded corners and a small honest caption."""
    x, y, w, h = box
    src = Image.open(HERE / "ingame-lab.jpg").convert("RGB").crop(CROP)
    scale = max(w / src.width, h / src.height)
    shot = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    ox, oy = (shot.width - w) // 2, (shot.height - h) // 2
    shot = shot.crop((ox, oy, ox + w, oy + h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=28, fill=255)
    img.paste(shot, (x, y), mask)
    d.rounded_rectangle((x - 1, y - 1, x + w, y + h), radius=29, outline=c["frame"], width=3)
    cap = "IN-GAME  ·  LIVE MUJOCO"
    cf = font("IBMPlexMono-Medium.ttf", caption_size)
    cw = d.textlength(cap, font=cf)
    cx, cy = x + 26, y + 26
    d.rounded_rectangle((cx, cy, cx + cw + 40, cy + caption_size * 2), radius=caption_size, fill=INK)
    d.text((cx + 20, cy + caption_size), cap, font=cf, fill=CREAM, anchor="lm")


def text_block(d, c, lx, top, max_w, k=1.0):
    eb = font("IBMPlexMono-Medium.ttf", round(32 * k))
    label = "OMO RESEARCH  ·  RP1"
    tw = d.textlength(label, font=eb)
    d.rounded_rectangle((lx - 4, top, lx + tw + 44 * k, top + 58 * k), radius=29 * k, fill=c["pill_bg"])
    d.text((lx + 20 * k, top + 29 * k), label, font=eb, fill=c["pill_fg"], anchor="lm")
    l1, l2 = "A real humanoid,", "standing in Minecraft."
    serif = fit(d, l2, "CrimsonText-SemiBold.ttf", round(146 * k), max_w)
    d.text((lx, top + 98 * k), l1, font=serif, fill=c["fg"])
    d.text((lx, top + 260 * k), l2, font=serif, fill=c["fg"])
    tag = "RoboParty's RP1 and its real policies, simulated live."
    d.text((lx, top + 440 * k), tag, font=fit(d, tag, "CrimsonText-Italic.ttf", round(56 * k), max_w), fill=c["sub"])
    d.rectangle((lx, top + 544 * k, lx + 120 * k, top + 549 * k), fill=ORANGE)
    facts = "MuJoCo  ·  RoboParty RP1 policies  ·  Fabric 26.2"
    d.text((lx, top + 576 * k), facts, font=fit(d, facts, "IBMPlexMono-Regular.ttf", round(33 * k), max_w),
           fill=c["fg"])
    d.text((lx, top + 702 * k), "omo.sh", font=font("IBMPlexMono-Medium.ttf", round(30 * k)), fill=c["sub"])


def hero(theme):
    c = THEMES[theme]
    W, H, PAD = 2400, 960, 48
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    ph = H - 2 * PAD
    pw = round(ph * (CROP[2] - CROP[0]) / (CROP[3] - CROP[1]))
    px = W - PAD - pw
    plate(img, d, c, (px, PAD, pw, ph), 22)
    lx = 140
    text_block(d, c, lx, 150, px - lx - 80)
    d.rectangle((0, 0, W - 1, H - 1), outline=c["frame"], width=4)
    out = img.resize((1600, 640), Image.LANCZOS)
    name = "hero.png" if theme == "light" else "hero-dark.png"
    out.save(HERE / name, optimize=True)
    print("wrote", HERE / name, out.size)


def social():
    """1280x640 GitHub social preview (Settings > Social preview), light theme."""
    c = THEMES["light"]
    W, H, PAD = 2560, 1280, 72
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    ph = H - 2 * PAD
    pw = round(ph * (CROP[2] - CROP[0]) / (CROP[3] - CROP[1]))
    px = W - PAD - pw
    plate(img, d, c, (px, PAD, pw, ph), 28)
    lx = 150
    text_block(d, c, lx, 200, px - lx - 90, k=1.25)
    d.rectangle((0, 0, W - 1, H - 1), outline=c["frame"], width=6)
    out = img.resize((1280, 640), Image.LANCZOS)
    out.save(HERE / "social-preview.png", optimize=True)
    print("wrote", HERE / "social-preview.png", out.size)


if __name__ == "__main__":
    for t in THEMES:
        hero(t)
    social()
