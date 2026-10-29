# Changelog

## Unreleased: first public source

The first public source drop of Omo RP1. No release build yet: build the mod from source.

- **Sidecar** (GPL-3.0-only): RoboParty's RP1 (24-DoF) in MuJoCo at 500 Hz physics / 50 Hz policy, driven by
  RoboParty's own ONNX policies: `policy.onnx` for walk, turn, follow and stand; `policy_wave.onnx` (motion
  tracking) for the wave; `policy_ufo.onnx` with a dance latent for a 30 s dance. Heading hold, a leash, walk-home,
  fall detection and auto-reset, a talking gesture for voice. Loopback HTTP API on 127.0.0.1:8797.
- **Setup**: `sidecar/setup.sh` creates the venv and fetches RoboParty's model, policies and configs onto your
  machine at pinned commits (nothing of theirs is redistributed); the in-game mesh cache is built on first run.
- **Mod** (MIT; Fabric, MC 26.2, client-only): renders the RP1 articulated (25 bodies, ~19k triangles with baked
  AO), interpolates poses, works with Sodium and Iris. `!rp1` chat commands, `!rp1 here` anchoring, scale.
- **Voice** (optional): a Simple Voice Chat plugin with positional replies from the robot's head and captions.
  Bring your own backend; ours is not included.
