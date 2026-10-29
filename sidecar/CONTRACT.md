# Omo RP1: sidecar ⇄ mod contract

RoboParty's open-source RP1 humanoid (24-DoF variant) runs in a local CPU MuJoCo sidecar (`sidecar/`) with
RoboParty's own pretrained ONNX policies, and a Fabric client mod (`mod/`) renders it, articulated, in a
Minecraft 26.2 world. Same architecture as [Omo Microduck](https://github.com/omoresearch/omo-microduck).
Anything that speaks this API can drive the robot.

Upstream files (fetched on your machine by `fetch_upstream.py`, never redistributed here; all GitHub, org Roboparty):
- Model: `RP1_description` -> `mjcf/robot_24dof.xml` (+ `mjcf/rp1_flat.xml` scene) and the body `meshes/*.STL`
  (no `meshes/hand/`).
- Policies + deploy config: `roboparty_inference` -> `robots/rp1_24dof/{robot.yaml,configs/*.yaml,models/*.onnx,motions/*.npz,latents/*.npz}`.
  Obs construction reference: `roboparty_inference/src/obs_manager.cpp`, `inference_node.cpp`.
- Training/sim2sim reference: `robolab` (actuator + env configs the policies were trained with).

## Sidecar HTTP API - 127.0.0.1:8797 only (never 0.0.0.0), JSON, no auth
Sim frame: MuJoCo world, z-up, metres, floor plane at z=0. Quaternions are [w,x,y,z].

- `GET /health` -> `{"ok":true,"mode":"stand","fallen":false,"t":12.3,"policy":"policy.onnx","hz":50}`
  (+ extras: `leash, wave, resets, falls, seq, overruns, step_ms, player, pos, yaw, cmd_vel, talk, talking, talks`).
- `GET /model.json` -> `{"version":1,"bodies":[{"name":"base_link","meshes":[{"rgba":[r,g,b,a],"verts":[x,y,z,...],"tris":[i,j,k,...]}]}]}`
  verts BODY-LOCAL (geom pos/quat baked), metres, visual geoms only, total <= ~20k triangles, MJCF colours.
  The first body is the root (`base_link`).
- `GET /pose` -> `{"seq":123,"t":12.34,"mode":"walk","fallen":false,"wave":0,"bodies":{"base_link":[px,py,pz,qw,qx,qy,qz],...}}`
  world transform of every body in /model.json.
- `POST /cmd` `{"cmd":..., "from":"mc"|"twitch:<user>"}` -> `{"ok":true,"mode":...}` / `{"ok":false,"error":...}`
  cmds: `walk` (forward ~0.4 m/s; optional `vx`,`vy`,`wz` override), `stop`, `turn` + `"dir":"left"|"right"` (~90 deg then stop),
  `follow`, `wave` (plays the wave policy/motion once, then back to stand), `reset` (respawn upright at origin),
  `dance` (RoboParty's `policy_ufo` with a dance latent, 30 s, then stand), `home` (walk back to the origin), `stand`,
  `talk` (`"on":true|false`, optional `"level":0..1`; a small talking gesture while standing).
  `left`/`right` = turn aliases. `from:"twitch:*"` may not `reset` or `talk` (403).
- `POST /player` `{"x":..,"y":..}` player position in sim-frame metres (~5 Hz), used by `follow` (stops within ~1.0 m).
- Leash: never further than `leash` metres (default 6) from the sim origin; steers back. `POST /config {"leash":r}` (0.5-100).
- Real time at the policy rate (50 Hz: dt 0.002, decimation 10), single-threaded (OMP_NUM_THREADS=1, onnxruntime
  intra/inter op threads = 1), Nice=10, so it never steals CPU from the game.
- Falls: no get-up policy exists for the RP1, so after ~3 s down the sidecar auto-`reset`s.

## Minecraft mapping (done in the mod)
Anchor A = (ax, ay, az) MC block coords, scale s (default 1.0: 1 sim metre = 1 block, the RP1 is about human size).
MC position of sim point p=(x,y,z): (ax + s*x, ay + s*z, az - s*y). Rotations R_mc = C R_sim C^T with C(x,y,z)=(x,z,-y).
Player (MC px,pz) -> sim x=(px-ax)/s, y=-(pz-az)/s.

## Chat commands (MC chat, intercepted client-side, never sent to the server)
`!rp1 walk|stop|left|right|follow|wave|dance|reset`, MC-only `!rp1 here` (anchor at your feet, persisted)
and `!rp1 scale <0.25-10>`. Reply prefix `[RP1]`.

## Voice (optional, mod side)
With Simple Voice Chat, the mod streams push-to-talk audio to a loopback WebSocket (default `ws://127.0.0.1:8792/svc`):
binary frames = PCM16 LE mono 48 kHz both ways; text frames = small JSON (out: `hello`, `ptt` on/off; in: `status`,
`transcript`, `clear`). The backend that answers is yours to provide; it may call `POST /cmd {"cmd":"talk"}` while
the robot speaks.
