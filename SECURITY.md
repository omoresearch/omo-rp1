# Security

Omo RP1 is local-first. At runtime nothing in it talks to the internet; `sidecar/setup.sh` and
`mod/deps/fetch.py` download from GitHub, Modrinth and the Simple Voice Chat Maven repository only when you run them.

## The model

- The sidecar listens on **127.0.0.1 only** (port 8797). It has no auth, so do not port-forward or
  reverse-proxy it.
- The mod is **client-side**. `!rp1` chat lines are intercepted and cancelled on your client; they never
  reach the server.
- The optional voice link only accepts a **loopback** WebSocket URL (it carries your microphone).
- Commands tagged `from: "twitch:*"` cannot `reset` the robot or trigger `talk`. The anchor and scale are set
  only from your own chat, in the mod.
- The policies are ONNX graphs fetched from RoboParty's repository at pinned commits; onnxruntime runs them as
  data, and no code from an upstream repository is executed.

## Reporting

Please report vulnerabilities privately to **harry@omo.space** with the subject
"Omo security", not in a public issue. Include the version, what an attacker
needs, and how to reproduce it. We aim to reply within a few days.
