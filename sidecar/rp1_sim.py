# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Omo Research. Part of Omo RP1 (sidecar/, GPL-3.0-or-later: see sidecar/LICENSE).
"""RP1 (24-DoF) MuJoCo simulation core (no HTTP).

Mirrors Roboparty `roboparty_inference` (src/inference_node.cpp + src/obs_manager.cpp) with
robots/rp1_24dof/configs/{default,beyondmimic,ufo}.yaml:
  * physics dt 0.002 s, decimation 10 -> 50 Hz policy
  * locomotion policy.onnx: obs "ang_vel:3, gravity_b:3, cmd_vel:3, dof_pos:24, dof_vel:24, last_action:24",
    frame_stack 3, obs_major ("term_major" in robolab): per field [t-2, t-1, t] -> 243
  * wave (beyondmimic) policy_wave.onnx: "motion_command:48 (ref joint pos 24 + vel 24), ang_vel, gravity_b,
    dof_pos, dof_vel, last_action", no stack -> 126; motion frame advances once per policy step
  * dance (ufo) policy_ufo.onnx: current state + sparse history taps t-4..t-1 + 256-D latent (gamma 0.8,
    window 3, norm-matched to frame 0), action = clip(out, -1, 1) * 8 * 0.4
  * target = default + action * action_scale (0.25), joint order = URDF order = MJCF actuator order
  * act_alpha 0.33 low-pass applied at the 500 Hz control rate (apply_action thread), with the 0.25 s
    alpha ramp (0.02 -> 0.33, smoothstep) after every policy switch
  * PD torque per physics step (the MJCF actuators are plain torque motors): robolab RP1_24DOF_CFG gains
    (the gains the policies were trained with), clipped to the MJCF ctrlrange and the robolab DC-motor envelope.
"""

import math
import os
import threading

import numpy as np

os.environ.setdefault("OMP_NUM_THREADS", "1")
import mujoco  # noqa: E402
import onnxruntime as ort  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROBOT_XML = os.path.join(HERE, "upstream", "RP1_description", "mjcf", "robot_24dof.xml")
RP1_DIR = os.path.join(HERE, "upstream", "roboparty_inference", "robots", "rp1_24dof")
MODEL_DIR = os.path.join(RP1_DIR, "models")

TIMESTEP = 0.002
DECIMATION = 10
CONTROL_DT = TIMESTEP * DECIMATION  # 0.02 s -> 50 Hz
ACT_ALPHA = 0.33
TRANSITION_S = 0.25
ACTION_SCALE = 0.25

JOINTS = (
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint",
    "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint",
    "right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint",
    "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_roll_joint", "waist_yaw_joint",
    "left_shoulder_pitch_joint", "left_shoulder_roll_joint", "left_shoulder_yaw_joint",
    "left_elbow_joint", "left_wrist_joint",
    "right_shoulder_pitch_joint", "right_shoulder_roll_joint", "right_shoulder_yaw_joint",
    "right_elbow_joint", "right_wrist_joint",
)
NJ = len(JOINTS)
DEFAULT_POSE = np.array([
    -0.1, 0.0, 0.0, -0.3, -0.2, 0.0,
    0.1, 0.0, 0.0, 0.3, -0.2, 0.0,
    0.0, 0.0,
    0.2, 0.2, 0.0, -1.2, 0.0,
    -0.2, -0.2, 0.0, 1.2, 0.0], dtype=np.float32)


def _group(name):
    if any(k in name for k in ("waist", "hip_pitch", "hip_roll", "knee")):
        return "big"
    if "hip_yaw" in name or "shoulder_pitch" in name:
        return "mid"
    if "ankle" in name:
        return "ankle"
    return "small"


# robolab/assets/robots/roboparty.py RP1_24DOF_CFG (RobopartyActuatorCfg) - what the policies were trained with
_TRAIN = {  # kp, kd, effort limit, velocity limit, keep_velocity
    "big": (120.0, 7.5, 140.0, 13.61357, 8.37758),
    "mid": (35.0, 2.5, 55.0, 16.23156, 6.28319),
    "ankle": (25.0, 1.5, 64.0, 15.70796, 14.66077),
    "small": (12.5, 0.75, 32.0, 15.70796, 14.66077),
}
# robots/rp1_24dof/robot.yaml (hardware motor gains, motor order -> URDF order via urdf2motor)
_HW_KP_MOTOR = [120.0, 120.0, 90.0, 120.0, 40.0, 40.0, 150.0, 120.0, 120.0, 90.0, 120.0, 40.0, 40.0, 150.0,
                30.0, 30.0, 20.0, 30.0, 20.0, 30.0, 30.0, 20.0, 30.0, 20.0]
_HW_KD_MOTOR = [9.0, 9.0, 6.0, 9.0, 2.0, 2.0, 12.5, 9.0, 9.0, 6.0, 9.0, 2.0, 2.0, 12.5,
                2.5, 2.5, 1.5, 2.5, 1.5, 2.5, 2.5, 1.5, 2.5, 1.5]
_URDF2MOTOR = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 6, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
GAINS = os.environ.get("RP1_GAINS", "train")  # "train" (robolab) or "hw" (robot.yaml)

# locomotion command limits (default.yaml clip_cmd)
CLIP_CMD = ((-0.4, 0.6), (-0.4, 0.4), (-0.8, 0.8))

SPAWN_Z = 0.78  # MJCF base_link pos / robolab init_state
LEASH_DEFAULT = 6.0
LEASH_MARGIN = 1.0
WALK_VX = 0.4
TURN_WZ = 0.8
TURN_TIMEOUT = 8.0
FOLLOW_STOP = 1.0
FOLLOW_VX = 0.4
FALL_RESET_S = 3.0
FALL_GZ = -0.5          # gravity_z_upper in the configs (>60 deg tilt)
FALL_Z = 0.35           # base_link height (standing ~0.775, the dance squats down to ~0.46)
WAVE_HOLD_S = 0.6       # keep the wave policy this long after the last motion frame, then hand back
# policy.onnx in this sim achieves ~0.8x the commanded forward speed (0.4 -> 0.31 m/s, 0.5 -> 0.40, 0.6 -> 0.48)
# and stands still below ~0.25; API speeds are nominal m/s and get multiplied by this before clip_cmd.
SPEED_GAIN = float(os.environ.get("RP1_SPEED_GAIN", "1.25"))
DANCE_S = float(os.environ.get("RP1_DANCE_S", "30"))  # play this much of the 130 s ufo latent clip
HOME_STOP = 0.5
# talk gesture (voice: the voice backend sends `talk` on/off while the robot speaks): a small waist sway + right-forearm
# "explaining" bob added on top of the locomotion policy's joint targets, ONLY while standing; ramped in/out.
TALK_RAMP_S = 0.4       # envelope 0 -> 1 time
TALK_MAX_S = 30.0       # talk on auto-expires (a lost "off" never leaves it gesturing forever)
TALK_WAIST_YAW = 0.07   # rad amplitude (sway)
TALK_ELBOW = 0.22       # rad amplitude (right forearm bob)
TALK_SHOULDER = 0.12    # rad (right upper arm slightly forward)


def _make_session(path):
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])


def quat_rotate_inverse(q, v):
    w = q[0]
    xyz = q[1:4]
    t = np.cross(xyz, v) * 2
    return v - w * t + np.cross(xyz, t)


def yaw_of(q):
    w, x, y, z = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def load_model(visual=False):
    """Physics model. visual=False drops the (huge, non-colliding, group-2) CAD mesh geoms: they only matter for
    rendering/model.json and cost ~600 MB RSS (mesh + hull + BVH data). Bodies, inertias and collision capsules are
    unchanged, so dynamics are identical."""
    spec = mujoco.MjSpec.from_file(ROBOT_XML)
    spec.option.timestep = TIMESTEP
    if not visual:
        for g in list(spec.geoms):
            if g.type == mujoco.mjtGeom.mjGEOM_MESH and g.group == 2 and g.contype == 0 and g.conaffinity == 0:
                spec.delete(g)
        for msh in list(spec.meshes):
            spec.delete(msh)
    wb = spec.worldbody
    # rp1_flat.xml scene: floor plane + light (the scene includes "robot.xml", so we add it here instead)
    g = wb.add_geom()
    g.name = "floor"
    g.type = mujoco.mjtGeom.mjGEOM_PLANE
    g.size = [0, 0, 0.05]
    g.condim = 4
    g.contype = 1
    g.conaffinity = 15
    g.friction = [0.9, 0.2, 0.2]
    g.solref = [0.01, 1]
    g.rgba = [0.25, 0.32, 0.38, 1]
    lt = wb.add_light()
    lt.pos = [0, 0, 3.5]
    lt.dir = [0, 0, -1]
    lt.type = mujoco.mjtLightType.mjLIGHT_DIRECTIONAL
    model = spec.compile()
    model.opt.timestep = TIMESTEP
    return model


class Policy:
    """One ONNX policy + its observation history (obs_major stacking or ufo sparse taps)."""

    def __init__(self, name, path, kind):
        self.name = name
        self.file = os.path.basename(path)
        self.kind = kind  # "loco" | "wave" | "ufo"
        self.sess = _make_session(path)
        self.inp = self.sess.get_inputs()[0].name
        self.n_in = int(self.sess.get_inputs()[0].shape[-1])
        self.out = np.zeros(NJ, dtype=np.float32)  # ctx->output_buffer (after clip/rescale)
        if kind == "loco":
            self.fields = (3, 3, 3, NJ, NJ, NJ)
            self.stack = 3
            assert self.n_in == sum(self.fields) * self.stack, self.n_in
        elif kind == "wave":
            m = np.load(os.path.join(RP1_DIR, "motions", "wave.npz"))
            self.mpos = m["joint_pos"].astype(np.float32)
            self.mvel = m["joint_vel"].astype(np.float32)
            self.nframes = len(self.mpos)
            self.fps = int(m["fps"][0])
            self.fields = (2 * NJ, 3, 3, NJ, NJ, NJ)
            self.stack = 1
            assert self.n_in == sum(self.fields), self.n_in
        elif kind == "ufo":
            self.z = np.load(os.path.join(RP1_DIR, "latents", "dance1_subject2.npz"))["z"].astype(np.float32)
            self.gamma, self.window = 0.8, 3
            self.z0n = float(np.linalg.norm(self.z[0]))
            assert self.n_in == 54 + NJ + 312 + 256, self.n_in
        self.reset()

    def reset(self):
        self.buf = None
        self.frame = 0
        self.hist = []  # ufo: list of per-step dicts, newest last
        self.zframe = 0
        self.playing = False
        self.out[:] = 0

    # ---- latent loader (LatentLoader::next)
    def _latent(self):
        n = len(self.z)
        cnt = min(self.window, n - self.zframe)
        w = self.gamma ** np.arange(cnt, dtype=np.float32)
        o = (w[:, None] * self.z[self.zframe:self.zframe + cnt]).sum(0) / w.sum()
        on = float(np.linalg.norm(o))
        if self.z0n > 0 and on > 0:
            o = o * (self.z0n / on)
        if self.playing:
            self.zframe += 1
            if self.zframe == n:
                self.zframe = n - 1
                self.playing = False
        return o.astype(np.float32)

    def build(self, s, cmd):
        """s: dict with ang_vel, grav, dof_pos (rel), dof_vel. Returns the flat input vector."""
        if self.kind == "loco":
            cur = [s["ang_vel"], s["grav"], cmd, s["dof_pos"], s["dof_vel"], self.out.copy()]
            if self.buf is None:
                self.buf = [np.tile(f, (self.stack, 1)) for f in cur]
            else:
                for i, f in enumerate(cur):
                    self.buf[i] = np.vstack([self.buf[i][1:], f[None, :]])
            x = np.concatenate([b.reshape(-1) for b in self.buf])
        elif self.kind == "wave":
            fr = min(self.frame, self.nframes - 1)
            x = np.concatenate([self.mpos[fr], self.mvel[fr], s["ang_vel"], s["grav"], s["dof_pos"], s["dof_vel"],
                                self.out])
            self.frame = min(self.frame + 1, self.nframes - 1)
        else:  # ufo
            cur = {"la": self.out.copy(), "av": s["ang_vel"], "dp": s["dof_pos"], "dv": s["dof_vel"], "g": s["grav"]}
            if not self.hist:
                # first frame with a latent policy: older history zero, newest = current
                zero = {k: np.zeros_like(v) for k, v in cur.items()}
                self.hist = [zero] * 4
            past = self.hist[-4:]  # t-4 .. t-1, oldest first
            parts = [s["dof_pos"], s["dof_vel"], s["grav"], s["ang_vel"], cur["la"]]
            for k in ("la", "av", "dp", "dv", "g"):
                parts.extend(h[k] for h in past)
            parts.append(self._latent())
            x = np.concatenate(parts)
            self.hist = (self.hist + [cur])[-4:]
        return np.clip(x.astype(np.float32), -100.0, 100.0)

    def run(self, x):
        a = self.sess.run(None, {self.inp: x.reshape(1, -1)})[0][0].astype(np.float32)
        if self.kind == "ufo":
            self.out = 8.0 * np.clip(a, -1.0, 1.0)
            return self.out * 0.4 + DEFAULT_POSE
        self.out = np.clip(a, -100.0, 100.0)
        return self.out * ACTION_SCALE + DEFAULT_POSE


class RP1Sim:
    def __init__(self, leash=LEASH_DEFAULT, log=print, gains=GAINS, wave=True, dance=True, visual=False):
        self.log = log
        self.model = load_model(visual)
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.base_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        fj = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "base_link_freejoint")
        self.fq = int(m.jnt_qposadr[fj])
        self.fv = int(m.jnt_dofadr[fj])
        jids = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n) for n in JOINTS]
        assert min(jids) >= 0, "joint missing"
        self.jq = np.array([m.jnt_qposadr[j] for j in jids])
        self.jv = np.array([m.jnt_dofadr[j] for j in jids])
        # actuator for each policy joint (MJCF actuator order == URDF order, but map by name anyway)
        self.act = np.array([mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, n) for n in JOINTS])
        assert min(self.act) >= 0 and m.nu == NJ
        self.ctrl_lim = m.actuator_ctrlrange[self.act, 1].copy()
        grp = [_group(n) for n in JOINTS]
        if gains == "hw":
            self.kp = np.array([_HW_KP_MOTOR[_URDF2MOTOR[i]] for i in range(NJ)])
            self.kd = np.array([_HW_KD_MOTOR[_URDF2MOTOR[i]] for i in range(NJ)])
        else:
            self.kp = np.array([_TRAIN[g][0] for g in grp])
            self.kd = np.array([_TRAIN[g][1] for g in grp])
        eff = np.array([_TRAIN[g][2] for g in grp])
        vlim = np.array([_TRAIN[g][3] for g in grp])
        keep = np.array([_TRAIN[g][4] for g in grp])
        self.eff, self.vlim = eff, vlim
        self.sat = eff / (1.0 - keep / vlim)  # robolab dc_motor_envelope saturation effort
        self.gains = gains
        self.policies = {"loco": Policy("loco", os.path.join(MODEL_DIR, os.environ.get("RP1_LOCO", "policy.onnx")), "loco")}
        if wave:
            self.policies["wave"] = Policy("wave", os.path.join(MODEL_DIR, "policy_wave.onnx"), "wave")
        if dance:
            self.policies["dance"] = Policy("dance", os.path.join(MODEL_DIR, "policy_ufo.onnx"), "ufo")
        self.lock = threading.Lock()
        self.leash = float(leash)
        self.player = None
        self.resets = 0
        self.falls = 0
        self.waves = 0
        self.talks = 0
        self._ji = {n: i for i, n in enumerate(JOINTS)}
        self.t = 0.0
        self.steps = 0
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        m, d = self.model, self.data
        mujoco.mj_resetData(m, d)
        d.qpos[self.fq:self.fq + 3] = [0.0, 0.0, SPAWN_Z]
        d.qpos[self.fq + 3:self.fq + 7] = [1, 0, 0, 0]
        d.qpos[self.jq] = DEFAULT_POSE
        mujoco.mj_forward(m, d)
        # settle the spawn height: drop the robot so the lowest foot touches the floor
        foot = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, n) for n in ("left_ankle_roll_link", "right_ankle_roll_link")]
        low = min(d.xpos[b][2] for b in foot) - 0.035 - 0.01  # capsule centre 3.5 cm below, radius 1 cm
        d.qpos[self.fq + 2] -= low - 0.002
        mujoco.mj_forward(m, d)
        for p in self.policies.values():
            p.reset()
        self.target = DEFAULT_POSE.astype(np.float64).copy()   # act_
        self.applied = DEFAULT_POSE.astype(np.float64).copy()  # last_act_ (low-passed)
        self.trans_el = TRANSITION_S
        self.active = "loco"
        self.cmd = np.zeros(3, dtype=np.float32)
        self.mode = "stand"
        self.walk_cmd = (WALK_VX, 0.0, 0.0)
        self.walk_heading = 0.0
        self.turn_target = None
        self.turn_t0 = 0.0
        self.turn_sign = 1.0
        self.returning = False
        self.after = "stand"
        self.wave_end_t = None
        self.dance_t0 = 0.0
        self.fallen = False
        self.fallen_since = None
        self.tau = np.zeros(NJ)
        self.talk_on = False
        self.talk_level = 0.6
        self.talk_env = 0.0
        self.talk_since = 0.0

    def switch_policy(self, name):
        """InferenceNode::switch_policy: fresh history, carry over the last action, alpha ramp."""
        if name == self.active:
            return
        prev = self.policies[self.active]
        p = self.policies[name]
        p.reset()
        p.out[:] = prev.out if p.kind != "ufo" else prev.out
        self.active = name
        self.trans_el = 0.0
        self.target = self.applied.copy()

    # ------------------------------------------------------------ commands
    def command(self, cmd, **kw):
        if cmd == "talk":  # voice: gesture while speaking (never moves the feet; only applied while standing)
            on = kw.get("on", True)
            on = on if isinstance(on, bool) else str(on).lower() not in ("0", "false", "off", "no")
            if on and not self.talk_on:
                self.talks += 1
            if on:
                self.talk_since = self.t
                lv = kw.get("level")
                if lv is not None:
                    self.talk_level = float(np.clip(float(lv), 0.0, 1.0))
            self.talk_on = bool(on)
            return True, None
        if cmd == "reset":
            self.resets += 1
            self.reset()
            return True, None
        if self.fallen and cmd in ("walk", "turn", "follow", "wave", "dance", "home"):
            return False, "robot is down; it will reset itself"
        if cmd == "wave":
            if "wave" not in self.policies:
                return False, "wave policy not available"
            self.waves += 1
            self.after = "stand"  # contract: plays once, then back to stand
            self.mode = "wave"
            self.cmd[:] = 0
            self.switch_policy("wave")
            self.wave_end_t = None
            return True, None
        if cmd == "dance":
            if "dance" not in self.policies:
                return False, "dance policy not available"
            self.mode = "dance"
            self.switch_policy("dance")
            self.policies["dance"].playing = True
            self.dance_t0 = self.t
            return True, None
        if cmd in ("walk", "stop", "follow", "turn", "stand", "home"):
            if cmd == "walk":
                vx = float(kw.get("vx", WALK_VX))
                vy = float(kw.get("vy", 0.0))
                wz = float(kw.get("wz", 0.0))
                self.walk_cmd = (float(np.clip(vx, *CLIP_CMD[0])), float(np.clip(vy, *CLIP_CMD[1])),
                                 float(np.clip(wz, *CLIP_CMD[2])))
                target = "walk"
            elif cmd in ("stop", "stand"):
                target = "stand"
            elif cmd in ("follow", "home"):
                target = cmd
            else:
                dr = kw.get("dir", "left")
                if dr not in ("left", "right"):
                    return False, "dir must be left or right"
                self.turn_sign = 1.0 if dr == "left" else -1.0
                target = "turn"
            self._enter(target)
            return True, None
        return False, f"unknown cmd {cmd!r}"

    def _enter(self, mode):
        self.switch_policy("loco")
        self.mode = mode
        self.returning = False
        if mode == "walk":
            self.walk_heading = self.yaw()
        if mode == "turn":
            self.turn_target = wrap(self.yaw() + self.turn_sign * math.pi / 2)
            self.turn_t0 = self.t

    def set_player(self, x, y):
        self.player = (float(x), float(y))

    # ------------------------------------------------------------ helpers
    def pos(self):
        return self.data.qpos[self.fq:self.fq + 3]

    def quat(self):
        return self.data.qpos[self.fq + 3:self.fq + 7]

    def yaw(self):
        return yaw_of(self.quat())

    def grav(self):
        return quat_rotate_inverse(self.data.xquat[self.base_id].astype(np.float32),
                                   np.array([0, 0, -1.0], dtype=np.float32))

    def _goto(self, tx, ty, speed, stop_r):
        p = self.pos()
        dx, dy = tx - p[0], ty - p[1]
        dist = math.hypot(dx, dy)
        if dist < stop_r:
            return np.zeros(3, dtype=np.float32), True
        err = wrap(math.atan2(dy, dx) - self.yaw())
        wz = float(np.clip(1.5 * err, -TURN_WZ, TURN_WZ))
        vx = speed * SPEED_GAIN * max(0.0, math.cos(err)) if abs(err) < 0.8 else 0.0
        if dist < stop_r + 0.4:
            vx *= max(0.3, (dist - stop_r) / 0.4)
        return np.array([vx, 0.0, wz], dtype=np.float32), False

    def _high_level(self):
        mode = self.mode
        p = self.pos()
        r = math.hypot(p[0], p[1])
        if mode == "walk":
            ux, uy, uw = self.walk_cmd
            self.walk_heading = wrap(self.walk_heading + uw * CONTROL_DT)
            herr = wrap(self.walk_heading - self.yaw())
            wz = float(np.clip(uw + 1.5 * herr, *CLIP_CMD[2]))  # heading hold
            yaw = self.yaw()
            vwx = ux * math.cos(yaw) - uy * math.sin(yaw)
            vwy = ux * math.sin(yaw) + uy * math.cos(yaw)
            outward = (p[0] * vwx + p[1] * vwy) > 0
            margin = min(LEASH_MARGIN, 0.3 * self.leash)
            if not self.returning and r > self.leash - margin and outward:
                self.returning = True
            if self.returning:
                v, _ = self._goto(0.0, 0.0, FOLLOW_VX, 0.0)
                if r < max(0.3, self.leash * 0.4):
                    self.returning = False
                    self.walk_heading = self.yaw()
                return v
            return np.array([ux * SPEED_GAIN, uy * SPEED_GAIN, wz], dtype=np.float32)
        if mode == "home":
            v, done = self._goto(0.0, 0.0, FOLLOW_VX, HOME_STOP)
            if done:
                self.mode = "stand"
            return v
        if mode == "follow":
            if self.player is None:
                return np.zeros(3, dtype=np.float32)
            tx, ty = self.player
            tr = math.hypot(tx, ty)
            lim = max(0.0, self.leash - min(LEASH_MARGIN, 0.3 * self.leash))
            if tr > lim:
                tx, ty = tx * lim / tr, ty * lim / tr
                v, _ = self._goto(tx, ty, FOLLOW_VX, 0.25)
                return v
            v, _ = self._goto(tx, ty, FOLLOW_VX, FOLLOW_STOP)
            return v
        if mode == "turn":
            if self.turn_target is None:
                self.turn_target = wrap(self.yaw() + self.turn_sign * math.pi / 2)
                self.turn_t0 = self.t
            err = wrap(self.turn_target - self.yaw())
            if abs(err) < 0.06 or self.t - self.turn_t0 > TURN_TIMEOUT:
                self.mode = "stand"
                return np.zeros(3, dtype=np.float32)
            wz = float(np.clip(2.0 * err, -TURN_WZ, TURN_WZ))
            if abs(wz) < 0.3:
                wz = math.copysign(0.3, wz)
            return np.array([0.0, 0.0, wz], dtype=np.float32)
        return np.zeros(3, dtype=np.float32)

    def _talk_offset(self):
        """Joint-target offset of the talk gesture at full envelope (only arms + waist yaw, small)."""
        off = np.zeros(NJ)
        t = self.t
        a = 0.5 + 0.5 * self.talk_level
        off[self._ji["waist_yaw_joint"]] = TALK_WAIST_YAW * a * math.sin(2 * math.pi * 0.45 * t)
        off[self._ji["right_shoulder_pitch_joint"]] = -TALK_SHOULDER * a
        off[self._ji["right_elbow_joint"]] = TALK_ELBOW * a * (0.5 + 0.5 * math.sin(2 * math.pi * 1.3 * t))
        off[self._ji["left_elbow_joint"]] = -0.5 * TALK_ELBOW * a * (0.5 + 0.5 * math.sin(2 * math.pi * 0.9 * t + 1.0))
        return off

    def _talk_update(self):
        if self.talk_on and self.t - self.talk_since > TALK_MAX_S:
            self.talk_on = False
        want = self.talk_on and self.mode == "stand" and self.active == "loco" and not self.fallen
        rate = CONTROL_DT / TALK_RAMP_S
        if want:
            self.talk_env = min(1.0, self.talk_env + rate)
        else:  # leave faster than we enter (walking / waving / falling takes over at once)
            self.talk_env = max(0.0, self.talk_env - (rate if self.mode == "stand" else 4 * rate))
        if self.active != "loco" or self.fallen:
            self.talk_env = 0.0

    def _state(self):
        d = self.data
        return {
            "ang_vel": d.qvel[self.fv + 3:self.fv + 6].astype(np.float32),  # base-frame angular velocity (IMU gyro)
            "grav": self.grav().astype(np.float32),
            "dof_pos": (d.qpos[self.jq] - DEFAULT_POSE).astype(np.float32),
            "dof_vel": d.qvel[self.jv].astype(np.float32),
        }

    def _update_fall(self):
        g = self.grav()
        z = float(self.pos()[2])
        fallen = bool(g[2] > FALL_GZ or z < FALL_Z)
        if fallen and not self.fallen:
            self.falls += 1
            self.fallen_since = self.t
            p = self.pos()
            self.log(f"[sim] t={self.t:.2f} FALL detected (mode={self.mode} policy={self.active} z={z:.3f} "
                     f"gz={g[2]:.2f} pos=({p[0]:.2f},{p[1]:.2f}))")
        if not fallen:
            self.fallen_since = None
        self.fallen = fallen
        if fallen and self.fallen_since is not None and self.t - self.fallen_since > FALL_RESET_S:
            self.log(f"[sim] t={self.t:.1f} on the ground > {FALL_RESET_S}s -> auto reset")
            self.resets += 1
            self.reset()

    def step(self):
        """One 50 Hz control step (policy + 10 physics substeps with 500 Hz PD + action low-pass)."""
        if self.mode == "wave":
            w = self.policies["wave"]
            if w.frame >= w.nframes - 1:
                if self.wave_end_t is None:
                    self.wave_end_t = self.t
                elif self.t - self.wave_end_t > WAVE_HOLD_S:
                    self._enter(self.after)
        if self.mode == "dance":
            p = self.pos()
            if not self.policies["dance"].playing or self.t - self.dance_t0 > DANCE_S:
                self._enter("stand")
            elif math.hypot(p[0], p[1]) > self.leash - min(LEASH_MARGIN, 0.3 * self.leash):
                self.log(f"[sim] t={self.t:.1f} dance reached the leash -> walking home")
                self._enter("home")
        if self.active == "loco":
            v = self._high_level() if not self.fallen else np.zeros(3, dtype=np.float32)
            for i in range(3):
                v[i] = np.clip(v[i], *CLIP_CMD[i])
            self.cmd = v.astype(np.float32)
        else:
            self.cmd = np.zeros(3, dtype=np.float32)
        pol = self.policies[self.active]
        x = pol.build(self._state(), self.cmd)
        self.target = pol.run(x).astype(np.float64)
        self._talk_update()
        if self.talk_env > 1e-4:
            self.target = self.target + self.talk_env * self._talk_offset()
        m, d = self.model, self.data
        for _ in range(DECIMATION):
            alpha = ACT_ALPHA
            if self.trans_el < TRANSITION_S:
                pr = self.trans_el / TRANSITION_S
                wgt = pr * pr * (3 - 2 * pr)
                a0 = min(ACT_ALPHA, 0.02)
                alpha = a0 + wgt * (ACT_ALPHA - a0)
                self.trans_el = min(self.trans_el + TIMESTEP, TRANSITION_S)
            self.applied = alpha * self.target + (1 - alpha) * self.applied
            q = d.qpos[self.jq]
            qd = d.qvel[self.jv]
            tau = self.kp * (self.applied - q) - self.kd * qd
            # robolab dc_motor_envelope (four-quadrant torque-speed limit)
            corner = self.vlim * (1.0 + self.eff / self.sat)
            vel = np.clip(qd, -corner, corner)
            up = np.minimum(self.sat * (1.0 - vel / self.vlim), self.eff)
            lo = np.maximum(self.sat * (-1.0 - vel / self.vlim), -self.eff)
            tau = np.clip(tau, lo, up)
            d.ctrl[self.act] = np.clip(tau, -self.ctrl_lim, self.ctrl_lim)
            mujoco.mj_step(m, d)
        self.tau = d.ctrl[self.act].copy()
        self.t += CONTROL_DT
        self.steps += 1
        self._update_fall()

    def public_mode(self):
        return self.mode
