# Contributing

Issues and PRs are welcome, especially renderer fixes, new RoboParty policies wired into the sidecar
(`policy_amp`, `policy_rough`, `policy_interrupt` are fetched but not used yet), and macOS test reports.

- **Sidecar changes** (`sidecar/`, GPL-3.0-only): run `sidecar/test_headless.py` (under `nice`, it is
  CPU-bound). For API changes also run `sidecar/test_http.py` against a test server on a spare port
  (`RP1_PORT=8817 .venv/bin/python server.py`, then `test_http.py http://127.0.0.1:8817`); don't point it at a
  sidecar you are playing with, because it walks, turns and resets the robot.
- **Mod changes** (`mod/`, MIT): `mod/build.sh`, then `python3 mod/test/check_refs.py`, and
  `mod/build.sh test http://127.0.0.1:8799` against `python3 mod/test/fake_sidecar.py 8799`. Voice changes:
  `mod/build.sh voicetest` (needs `python3 mod/deps/fetch.py`).
- **API changes**: update `sidecar/CONTRACT.md` in the same PR.
- **Never commit RoboParty files**: nothing under `sidecar/upstream/`, no `*.STL`, `*.onnx`, `*.npz`, and no
  `sidecar/model_cache.json` (it is derived from RoboParty's meshes). `.gitignore` covers them.

By contributing you agree that your contribution is licensed under the licence of the directory you change
(MIT for `mod/` and `docs/`, GPL-3.0-only for `sidecar/`).

Keep the sidecar loopback-only and the mod client-side.
