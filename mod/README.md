# Omo RP1 client mod (Minecraft 26.2, Fabric)

Renders RoboParty's **RP1** humanoid (24-DoF), articulated, from the local sidecar
([`../sidecar`](../sidecar), HTTP on `127.0.0.1:8797`) at a fixed spot in the world, and turns `!rp1 ...`
chat lines into sidecar commands. Client-only; nothing is sent to the server. Contract:
[`../sidecar/CONTRACT.md`](../sidecar/CONTRACT.md). Same architecture as
[Omo Microduck](https://github.com/omoresearch/omo-microduck); the two mods coexist: each intercepts only its own
prefix (`!rp1` here, `!duck` in Microduck). Licence: MIT ([`../LICENSE`](../LICENSE)).

## Build / install / test

```
python3 deps/fetch.py          # optional: Simple Voice Chat jars (hash-checked; compile-only API + test jar)
./build.sh                     # javac against the unobfuscated 26.2 client jar + fabric-api nested jars
MC_MODS=/path/to/instance/mods ./build.sh install   # + atomic copy to $MC_MODS/rp1-1.0.0+mc26.2.jar, prints sha256
./build.sh test [URL]          # + standalone harness (test/, not in the jar) against URL (default :8797)
python3 test/fake_sidecar.py 8799   # stand-in sidecar (fake box humanoid, base_link first, wave counter)
python3 test/check_refs.py     # every MC/Fabric/JOML/SVC member the mod calls exists in the target jars (javap)
./build.sh voicetest           # PCM, config, head position, focus arbitration, real SVC Opus round trip
```

Paths come from env vars (`JAVA_HOME` or `JDK_BIN`, `PRISM` or `MC_LIBRARIES`, `MC_MODS` or `FABRIC_API_JAR`;
see the top of [`build.sh`](build.sh)). The defaults match an [Omo Space](https://github.com/omoresearch/omo-space)
install. `install` needs `MC_MODS` set explicitly and moves only older `rp1-*.jar` files out of mods/ (never any
other mod), so mods/ never holds two copies. The jar loads on the next game launch.

Run the harness against a test sidecar or `fake_sidecar.py`, not one you are playing with: it walks, turns and
resets the robot.

## Chat commands (typed in MC chat; intercepted, never sent to the server)

| command | effect |
|---|---|
| `!rp1` / `!rp1 help` | help |
| `!rp1 walk\|stop\|follow\|wave\|dance\|reset` | `POST /cmd {"cmd":..., "from":"mc"}` |
| `!rp1 left\|right` | `POST /cmd {"cmd":"turn","dir":...}` |
| `!rp1 here` | anchor = your feet block (x/z centre, floor y) + dimension; saved |
| `!rp1 scale <0.25-10>` | MC blocks per sim metre (default 1: RP1 is ~1.5 blocks tall); saved |

Replies show as `[RP1] ...` client system messages.

## Config: `config/rp1.properties` (in your game dir)

`anchor.x/y/z` (MC coords of the sim origin), `dim` (e.g. `minecraft:overworld`), `scale` (1.0),
`port` (8797), `enabled` (false = no rendering, no network). Written with defaults (no anchor) on
first launch. Edit while the game is closed or use the chat commands. Nothing renders until an
anchor is set (`!rp1 here`).

## How it works

* **Network** (`SidecarClient`, one low-priority daemon thread, `java.net.http`): fetch `/model.json`
  (15 s timeout, retry 1 s -> 15 s backoff), poll `/pose` at ~25 Hz, `POST /player` at ~5 Hz while you
  are in the anchor's dimension. The render/tick threads only read a volatile snapshot; HTTP never
  blocks them. Sidecar down -> one log line, the robot disappears, it reconnects (and refetches the
  model) quietly.
* **Model** (`Rp1Model`): body names and order come from `/model.json` (nothing hardcoded); the
  **root body is the first one** (`base_link`). On load, all visual meshes of a body are baked once
  into flat primitive arrays: MC-axis vertices (9 floats/tri), a flat unit normal (3 floats/tri) and
  an ARGB colour per triangle. Per frame, `Rp1Model.emit` reads the PoseStack matrix once per body and
  does 3 position + 1 normal transform per triangle, feeding vanilla's bulk
  `VertexConsumer.addVertex(x,y,z,color,u,v,overlay,light,nx,ny,nz)` (the path `ModelPart` uses) -
  no per-vertex allocation or matrix-stack calls. Harness: 20k triangles parse in ~40 ms (network
  thread, once) and transform in ~0.15 ms/frame.
* **Mapping** (`Rp1Math`): MC = A + s·C·p with C(x,y,z) = (x, z, -y); rotations R_mc = C R C^T, i.e.
  quaternion [w, x, z, -y]. Player MC (px, pz) -> sim ((px-ax)/s, -(pz-az)/s).
* **Rendering**: `LevelRenderEvents.COLLECT_SUBMITS` (fabric-rendering-v1) -> per body
  `SubmitNodeCollector.submitCustomGeometry(poseStack, RenderTypes.entityCutout(rp1:textures/misc/white.png), ...)`,
  each triangle as a degenerate quad. MJCF rgba as vertex colour; light = max of the block at the
  root's x/z just above the lowest body (feet) and the root's own block; poses interpolated (lerp +
  shortest-arc nlerp) between the last two sidecar poses. Skipped when the root body is > 96 blocks
  from the camera, the bounding box is outside the frustum, in another dimension, or without anchor.
  `entity_cutout` is vanilla's no-cull mob pipeline, so Sodium and Iris handle it like any mob.
* **Wave**: `pose.wave` is parsed (counter) but needs no client action; there is no sound.

## What to check in-game

1. `logs/latest.log`: `rp1: sidecar http://127.0.0.1:8797, scale 1.0, anchor unset` and
   `rp1: sidecar ... connected (model: N bodies, M triangles)` (N ~ 25, M <= ~20k).
2. If Microduck is installed too: `!duck stop` -> `[Duck] stop: ok ...`; `!rp1` does not trigger duck replies and
   vice versa.
3. Stand on a flat floor, `!rp1 here` -> `[RP1] anchor set to ...`. RP1 appears standing ~1.5 blocks tall, feet on
   the floor, coloured like the MJCF (white/grey/dark shells).
4. `!rp1 walk` (walks forward, legs articulate), `!rp1 left`, `!rp1 right`, `!rp1 stop`,
   `!rp1 follow` (walks toward you inside the 6 m leash), `!rp1 wave` (arm wave, then back to stand),
   `!rp1 dance`, `!rp1 reset`.
5. `!rp1 scale 2` -> ~3 blocks tall; `!rp1 scale 1` back.
6. FPS with RP1 in view vs. looking away: no noticeable drop; walking 100 blocks away makes it vanish
   (96-block cull) and it reappears when you come back.
7. With an Iris shader pack on and off: visible, coloured, lit like a mob; it darkens at night/in shade.
8. `!rp1 ...` lines never appear for other players / in the server log (cancelled client-side).
9. Stop the sidecar: RP1 disappears, one log line, no spam; start it again: it comes back by itself.

## Uninstall

Close the game, delete `mods/rp1-1.0.0+mc26.2.jar` (and optionally `config/rp1.properties`,
`config/rp1-voice.properties`).

## Voice (Simple Voice Chat, optional; bring your own backend)

With Simple Voice Chat installed, `Rp1VoicePlugin` (SVC `voicechat` entrypoint; `suggests` voicechat, so the mod
still loads without it) streams your push-to-talk (PCM16 LE mono 48 kHz) to a **local voice backend** at
`ws://127.0.0.1:8792/svc` while the RP1 holds the mic focus, and plays the reply on a SVC locational channel at his
head (`head_body` waist_yaw_link + `head_up` 0.33 sim m, i.e. ~1.2 blocks up at scale 1; falls back to base_link +
0.5 m). Captions show as `[RP1] ...`. **The backend is not in this repo** (ours is a private realtime-voice persona
called "Roboto"); the wire format is in [`../sidecar/CONTRACT.md`](../sidecar/CONTRACT.md#voice-optional-mod-side),
and `Rp1VoiceLink` / `Rp1VoicePlugin` are the reference client.

- Config `config/rp1-voice.properties`: `enabled`, `url` (loopback only), `hear_range` (12 blocks or `always`),
  `look_angle` (30), `voice_distance` (24), `captions`, `player`, `head_body`, `head_up`.
- Focus (`Rp1Focus`, a small shared System-property protocol so several Omo companions in one game agree on who
  listens; the Microduck plugin yields to any non-duck owner): within hear_range, looking within look_angle of his
  head (and not more directly at the duck) claims `2 + (1 - angle/180)`; otherwise being nearer to him than to the
  duck (and not looking at the duck) claims `1/(1+dist)`. The best fresh claim owns `omo.voice.focus`, so exactly
  one companion hears each push-to-talk.
- `./build.sh voicetest [ws://127.0.0.1:8792/svc a.pcm ...]`: PCM, config, head position, focus arbitration and a real
  SVC Opus round trip (+ an optional live run through your backend).
