# Third-party notices

Omo RP1's own code is split by directory (see [LICENSE](LICENSE)): `mod/` and `docs/` are MIT,
`sidecar/` is GPL-3.0-or-later ([sidecar/LICENSE](sidecar/LICENSE)). It builds on the following work. Thank you.

**Nothing from RoboParty is redistributed in this repository.** `sidecar/fetch_upstream.py` (run by
`sidecar/setup.sh`) downloads the files below from RoboParty's own GitHub repositories onto your machine, at pinned
commits, into `sidecar/upstream/` (git-ignored). The in-game mesh cache `sidecar/model_cache.json` is derived from
those meshes on your machine at first run and is git-ignored too.

| Component | By | Licence | How it is used |
|---|---|---|---|
| [RP1_description](https://github.com/Roboparty/RP1_description): RP1 24-DoF MJCF + body STL meshes | RoboParty | **No licence stated upstream** (all rights reserved by default) | **Fetched on your machine, not redistributed.** Loaded by MuJoCo; decimated locally into `model_cache.json` for the mod. Pinned commit `d8c6dd0`. |
| [roboparty_inference](https://github.com/Roboparty/roboparty_inference): RP1 ONNX policies (`policy.onnx`, `policy_wave.onnx`, `policy_ufo.onnx`), motions, latents, deploy configs, reference sources | RoboParty | GPL-3.0 | **Fetched on your machine, not redistributed.** `sidecar/rp1_sim.py` is a port of its observation/action pipeline (`src/inference_node.cpp`, `src/obs_manager.cpp`), which is why `sidecar/` is GPL-3.0-or-later. Pinned commit `7a11782`. |
| [robolab](https://github.com/Roboparty/robolab): RP1 actuator and env configs | RoboParty | BSD-3-Clause | **Fetched on your machine for reference, not redistributed.** The PD gains and motor envelope in `sidecar/rp1_sim.py` are taken from its RP1 training config. Pinned commit `9ccf7f2`. |
| [MuJoCo](https://github.com/google-deepmind/mujoco) | Google DeepMind | Apache-2.0 | Physics and offscreen rendering (pip dependency). |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Microsoft | MIT | Policy inference (pip dependency). |
| [NumPy](https://numpy.org) | NumPy developers | BSD-3-Clause | Arrays (pip dependency). |
| [Pillow](https://python-pillow.org) | Jeffrey A. Clark and contributors | MIT-CMU | Preview and README image scripts only (pip dependency). |
| [Fabric Loader / Fabric API](https://fabricmc.net) | FabricMC | Apache-2.0 | Compile and runtime dependency; not bundled. |
| [Simple Voice Chat API](https://modrinth.com/plugin/simple-voice-chat) | Max Henkel | see its project page | Optional, compile-only; fetched and hash-checked by `mod/deps/fetch.py`; not bundled. |
| Crimson Text, IBM Plex Mono | Sebastian Kosch; IBM | SIL OFL 1.1 | Typefaces used by `docs/media/make_hero.py` and `make_buttons.py`; not bundled. |

The RP1 is a robot designed by [RoboParty](https://github.com/Roboparty). This is an independent community
project, **not affiliated with or endorsed by RoboParty**. The images in `docs/media/` are frames recorded inside
Minecraft and offscreen MuJoCo renders made on our machine; they show the RP1 as rendered from RoboParty's model.

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR
MICROSOFT. Minecraft is a trademark of Mojang/Microsoft.
