# Sidecar: RoboParty's RP1 in MuJoCo, for Minecraft

Runs RoboParty's open-source **RP1** humanoid (24-DoF variant; MJCF + meshes from
[Roboparty/RP1_description](https://github.com/Roboparty/RP1_description)) with RoboParty's own pretrained ONNX
policies from [Roboparty/roboparty_inference](https://github.com/Roboparty/roboparty_inference)
(`robots/rp1_24dof`) on the CPU, in real time at 50 Hz, and serves its pose over HTTP on `127.0.0.1:8797` for the
Fabric mod. API contract: [`CONTRACT.md`](CONTRACT.md).

Licence: this directory is **GPL-3.0-or-later** ([`LICENSE`](LICENSE)), because `rp1_sim.py` ports the observation
and action pipeline of RoboParty's GPL-3.0 deploy code. The robot files and policies are **not** in this repository:
`fetch_upstream.py` downloads them from RoboParty's GitHub onto your machine at pinned commits (see
[`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)).

## Setup

```bash
./setup.sh        # .venv + requirements, fetch the RoboParty files (~81 MB) into upstream/, 2 s smoke test
```

## Run

```bash
OMP_NUM_THREADS=1 nice -n 10 .venv/bin/python server.py   # 127.0.0.1:8797; RP1_PORT=8817 (or --port) to move it

# tests
.venv/bin/python test_headless.py        # no HTTP, faster than real time, PASS/FAIL summary
.venv/bin/python test_http.py [base_url] # against a running server (default http://127.0.0.1:8797), ~45 s
.venv/bin/python stress.py 10 1          # optional: 10 sim-minutes of random commands (seed 1)
.venv/bin/python render_preview.py       # offscreen PNGs -> preview/ (MUJOCO_GL=egl)
.venv/bin/python fetch_upstream.py       # re-fetch the needed upstream files into upstream/ (idempotent)
```

`test_http.py` walks, turns and resets the robot: point it at a server you are not playing with
(`RP1_PORT=8817 .venv/bin/python server.py`, then `test_http.py http://127.0.0.1:8817`).

User service: [`contrib/rp1-sim.service`](contrib/rp1-sim.service) (copy to `~/.config/systemd/user/`; `Nice=10`,
`OMP_NUM_THREADS=1`, `Restart=on-failure`), then `journalctl --user -u rp1-sim -f` shows commands, falls and
auto-resets. The first start builds `model_cache.json` (the decimated in-game mesh, ~40 s, derived from the fetched
meshes, never committed); later starts load it in well under a second.

Measured CPU (one core, `/proc/<pid>/stat` over 20–30 s): **stand 8.1 %, walk 8.0 %, dance 15.5 %**; single sim
thread; RSS ≈ 160 MB. Step cost 0.7 ms (policy.onnx) / 2.1 ms (policy_ufo.onnx) of the 20 ms budget.

If `127.0.0.1:8797` is already taken, the server keeps simulating and retries the bind every 10 s (logged on the
first failure and every 5 min) instead of crash-looping.

## Endpoints (summary — see CONTRACT.md)

| | |
|---|---|
| `GET /health` | `{ok, mode, fallen, t, policy, hz}` + extras: `leash, wave, resets, falls, seq, overruns, step_ms, player, pos, yaw, cmd_vel, extras` |
| `GET /model.json` | 25 bodies, 18 979 triangles (~1.9 MB with normals/colours), body-local verts (geom pos/quat baked), MJCF material rgba |
| `GET /pose` | `{seq, t, mode, fallen, wave, bodies:{name:[px,py,pz,qw,qx,qy,qz]}}`, world frame, every model.json body |
| `POST /cmd` | `walk` (opt. `vx,vy,wz`), `stop`, `turn` + `dir`, `left`/`right` (aliases), `follow`, `wave`, `reset`, extras `dance`, `home`, `stand`, `talk` (`on`, opt. `level` 0-1; voice gesture). `from:"twitch:*"` may not `reset`/`talk` (403). |
| `POST /player` | `{x,y}` sim-frame metres, used by `follow` |
| `POST /config` | `{leash: r}` (0.5–100 m, default 6) |

Bodies (model.json / pose): `base_link`, `{left,right}_{hip_pitch,hip_roll,hip_yaw,knee,ankle_pitch,ankle_roll}_link`,
`waist_roll_link`, `waist_yaw_link`, `{left,right}_{shoulder_pitch,shoulder_roll,shoulder_yaw,elbow,wrist}_link`.
Modes reported: `stand`, `walk`, `turn`, `follow`, `wave`, `dance`, `home` (walking back to the origin).
`model.json` meshes also carry v1-compatible extras like the Microduck sidecar (omoresearch/omo-microduck): `name`, `normals`, `colors` (MJCF colour ×
baked same-body AO), `cull` (always false here: clustered CAD meshes are not closed).

## Design

- `rp1_sim.py` — sim core (no HTTP). Replicates `roboparty_inference` (`inference_node.cpp`, `obs_manager.cpp`)
  with `configs/default.yaml` + `beyondmimic.yaml` + `ufo.yaml`:
  - physics dt 0.002 s, decimation 10 → 50 Hz policy; `robot_24dof.xml` + the `rp1_flat.xml` floor
    (plane, friction 0.9, condim 4) built with MjSpec. The physics model drops the visual CAD mesh geoms
    (group 2, non-colliding — dynamics unchanged; they cost ~600 MB RSS); `model.json`/previews load them.
  - **Joint order**: policy/URDF order (`usd2urdf` identity) = robolab `RP1_JOINT_ORDER` = MJCF actuator order
    (left leg 6, right leg 6, waist roll/yaw, left arm 5, right arm 5); mapped by name to qpos/dof/actuator anyway.
  - **Locomotion** `policy.onnx` (in 243): `ang_vel(3) gravity_b(3) cmd_vel(3) dof_pos−default(24) dof_vel(24)
    last_action(24)`, 3-frame history, obs-major (`term_major`) stacking `[f(t−2), f(t−1), f(t)]` per field, first
    frame replicated. ang_vel = base-frame free-joint angular velocity (IMU at the base_link origin), gravity from the
    base_link quaternion.
  - action → target = `default + 0.25·action`; the deploy's `act_alpha 0.33` low-pass runs at the 500 Hz control
    rate (every physics step) like `apply_action()`, including the 0.25 s smoothstep alpha ramp (0.02 → 0.33)
    after a policy switch. `last_action` obs = raw (clipped/rescaled) policy output, as in the deploy.
  - **PD in Python** each physics step (the MJCF actuators are plain torque motors): robolab `RP1_24DOF_CFG` gains
    — the ones the policies were trained with — kp/kd 120/7.5 (waist, hip pitch/roll, knee), 35/2.5 (hip yaw,
    shoulder pitch), 25/1.5 (ankles), 12.5/0.75 (shoulder roll/yaw, elbow, wrist); torque clipped to robolab's
    four-quadrant DC-motor envelope and the MJCF ctrlrange. Ankles are driven directly as joints (the hardware's
    closed-chain `decouple_rp1` is not needed: the policy outputs URDF joint targets). MJCF armature, damping 0.01
    and frictionloss 0.1 are kept (they match robolab's actuator armature/friction).
  - **Wave** `policy_wave.onnx` (BeyondMimic student, in 126): `motion_command(48 = wave.npz joint_pos+joint_vel of
    the current frame) ang_vel gravity_b dof_pos dof_vel last_action`, no stacking; the motion frame advances once
    per policy step (218 frames @ 50 fps = 4.4 s) and clamps at the end; 0.6 s after the last frame control returns
    to `policy.onnx` / `stand`.
  - **Dance** (extra) `policy_ufo.onnx` (in 646, embedded normaliser): `dof_pos dof_vel gravity_b ang_vel
    last_action` at t, then history taps t−4..t−1 for `last_action, ang_vel, dof_pos, dof_vel, gravity_b`, then the
    256-D latent from `latents/dance1_subject2.npz` (`LatentLoader`: γ 0.8 window-3 average, norm-matched to frame 0);
    action = clip(out, ±1)·8 (`action_rescale`)·0.4 + default. Plays `RP1_DANCE_S` (30) s of the 130 s clip, or
    stops early and walks `home` if it reaches the leash.
- High-level control (50 Hz, on top of the policy command; `cmd_vel` clipped to `clip_cmd` vx[−0.4,0.6]
  vy[−0.4,0.4] wz[−0.8,0.8]):
  - `walk`: heading hold (`wz += 1.5·heading error`), speed gain (see limitations): default 0.4 m/s nominal →
    policy 0.5.
  - `turn`: wz ≤ 0.8 rad/s (P on the yaw error, min 0.3) until within 0.06 rad of ±90°, then `stand` (8 s timeout).
  - `follow`: steer toward the last `/player` position, stop within 1.0 m; targets clamped inside the leash.
  - leash: walking outward past `leash − min(1, 0.3·leash)` switches to “return to origin” until within 0.4·leash,
    then continues in the new heading.
- Falls: projected gravity z > −0.5 (`gravity_z_upper`, > 60° tilt) or base_link z < 0.35 m → `fallen`; no get-up
  policy exists for RP1, so after 3 s down → auto-`reset` (respawn upright at the origin, feet on the floor).
- `server.py` — same design as the Microduck sidecar: stdlib `ThreadingHTTPServer` on 127.0.0.1; one sim thread steps
  under a lock with an absolute 50 Hz schedule (resyncs instead of spiralling if > 250 ms behind, counted in
  `overruns`) and publishes a pre-serialised `/pose` snapshot; HTTP threads only read snapshots or apply commands
  under the lock.
- `model_export.py` — visual geoms = mesh geoms in group 2 with contype/conaffinity 0; 19 k triangle budget split
  by surface area (min 60 per mesh); vertex clustering with a binary-searched grid cell per mesh (quadric
  simplification stalls on CAD STLs); crease-split smooth normals; baked AO. Cached in `model_cache.json`
  (signature = export params + MJCF text + STL sizes; delete it to rebuild, ~40 s).
- `upstream/` — only the needed files: `RP1_description/{mjcf/robot_24dof.xml, mjcf/rp1_flat.xml, meshes/*.STL
  (body, no hands)}`, `roboparty_inference/{robots/rp1_24dof/**, src/obs_manager.cpp, inference_node.{cpp,hpp},
  utils/{motion,latent}_loader.*, utils/decouple_rp1.cpp}`, `robolab/` actuator + env configs (reference).

## Measured (test_headless.py / test_http.py / stress.py)

- stand 20 s: 0 falls, drift 3 mm, base z 0.775 m.
- walk (nominal 0.4 → policy 0.5): **0.395 m/s** forward, 4 cm lateral drift over 10 s, yaw +1.5°.
- turn left/right: **+86° / −85° in 2.6 s**, then stands.
- follow: stops 1.07–1.10 m from the player; out-of-leash targets clamped (max r 4.7 m with leash 6).
- wave: 4.96 s, mean |q − reference| 0.076 rad, right shoulder pitch peaks 2.31 rad (arm overhead), 0 falls.
- dance: 30 s segment 0 falls (deep squats to base z 0.47 m); the full 130 s clip also ran with 0 falls but wanders
  ~8 m, hence the 30 s / leash limit.
- forced fall (shove): detected after 0.46 s, auto-reset 3.00 s later, upright again.
- stress: 2 × 10 sim-minutes of random commands (walk/vx-vy-wz overrides/turn/follow/wave/dance), 0 falls, 0 resets.

## Known limitations / deviations

- **Speed**: in this sim `policy.onnx` achieves ~0.8× the commanded forward speed (0.4 → 0.31 m/s, 0.5 → 0.40,
  0.6 → 0.48) and does not translate below ~0.25 (steps in place). API velocities are multiplied by
  `RP1_SPEED_GAIN` (default 1.25) before `clip_cmd`, so `vx` overrides are nominal m/s; max achievable ≈ 0.48 m/s.
- **Gains**: the hardware `robot.yaml` gains (motor space, e.g. ankle motors 40/2, hip yaw 90/6) were tried
  (`RP1_GAINS=hw`): the robot stands and turns but does **not** walk forward (0.0 m/s at cmd 0.4), so the robolab
  training gains are used (default `RP1_GAINS=train`).
- No actuator delay randomisation / observation noise (training has 0–2 physics-step delays and noise); no IMU
  sensor noise (MuJoCo's `noise=` attributes are metadata only).
- `policy_amp.onnx`, `policy_rough.onnx`, `policy_interrupt.onnx` are fetched but not wired (`RP1_LOCO=policy_amp.onnx`
  swaps the locomotion model; untested). No get-up policy → falls always end in a reset.
- Only a flat floor; the robot can't see Minecraft blocks. `/player` is not timed out (follow uses the last position).
- `wave` always returns to `stand` (contract), even if it was following before.

## Talk gesture (voice)

`POST /cmd {"cmd":"talk","on":true|false,"level":0..1}` (sent by your voice backend while the robot speaks; ours is not in this repo): a small
waist-yaw sway (0.07 rad) + right-forearm bob (0.22 rad) and slightly raised right upper arm, added to the locomotion
policy's joint targets ONLY in `stand` mode (ramped in over 0.4 s, out faster when he walks/waves/dances; never on
the wave/dance policies), auto-off 30 s after the last `on`. `/health` adds `talk`, `talking` (envelope), `talks`.
Measured: 25 s talking while standing: 0 falls, 2 mm drift, elbow range 0.23 rad, waist 0.10 rad; stress 2 x 10
sim-min with random talk on/off mixed into the command stream: 0 falls, 0 resets. Yaw creep while talking ~3.5 deg
per 30 s (the wave itself turns him ~17 deg; pre-existing).
