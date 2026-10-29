package dev.yifan.rp1;

import java.io.IOException;
import java.nio.file.Path;
import java.util.concurrent.ConcurrentLinkedQueue;

import org.joml.Quaternionf;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.rendering.v1.level.LevelRenderContext;
import net.fabricmc.fabric.api.client.rendering.v1.level.LevelRenderEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.renderer.culling.Frustum;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.rendertype.RenderTypes;
import net.minecraft.client.renderer.state.level.CameraRenderState;
import net.minecraft.client.renderer.texture.OverlayTexture;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.resources.Identifier;
import net.minecraft.util.LightCoordsUtil;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

/**
 * Renders the RoboParty RP1 humanoid from the local rp1-sim sidecar, articulated, at a fixed anchor in the world,
 * and turns `!rp1 ...` chat lines into sidecar commands (client-side only; never sent to the server). Only the
 * `!rp1` prefix is intercepted, so other mods' prefixes (e.g. microduck's `!duck`) pass through untouched.
 */
public final class Rp1 implements ClientModInitializer {
    public static final String ID = "rp1";
    static final Logger LOG = LoggerFactory.getLogger(ID);

    static final Identifier WHITE = Identifier.fromNamespaceAndPath(ID, "textures/misc/white.png");
    static final double MAX_DIST = 96.0;
    /** Nominal standing height of the robot in sim metres (for the scale reply only). */
    static final double HEIGHT_M = 1.5;

    static Rp1Config config;
    static Path configFile;
    static Rp1VoiceConfig voiceConfig = new Rp1VoiceConfig();
    static SidecarClient client;
    /** Set by Rp1VoicePlugin while Simple Voice Chat's server runs (SVC installed + world open). */
    static volatile boolean voiceActive;
    static volatile boolean focusOwned;
    static RenderType renderType;
    static final ConcurrentLinkedQueue<Component> REPLIES = new ConcurrentLinkedQueue<>();
    /** Scratch matrices for the geometry callbacks (they run one after another on the render thread). */
    static final float[] MAT = new float[16];
    static final float[] NRM = new float[9];

    @Override
    public void onInitializeClient() {
        configFile = FabricLoader.getInstance().getConfigDir().resolve("rp1.properties");
        config = Rp1Config.load(configFile);
        saveConfig();                                  // write defaults on first run
        Path vf = FabricLoader.getInstance().getConfigDir().resolve("rp1-voice.properties");
        voiceConfig = Rp1VoiceConfig.load(vf);
        try {
            voiceConfig.save(vf);
        } catch (IOException e) {
            LOG.warn("rp1 voice: cannot write {}: {}", vf, e.toString());
        }
        client = new SidecarClient(() -> config.port, () -> config.enabled, s -> LOG.info("rp1: {}", s));
        client.start();
        ClientSendMessageEvents.ALLOW_CHAT.register(Rp1::allowChat);
        ClientTickEvents.END_CLIENT_TICK.register(Rp1::tick);
        LevelRenderEvents.COLLECT_SUBMITS.register(Rp1::render);
        LOG.info("rp1: sidecar {}, scale {}, anchor {}", client.base(), config.scale,
                config.hasAnchor() ? config.ax + "," + config.ay + "," + config.az + " in " + config.dim : "unset");
    }

    // ---------------------------------------------------------------- chat

    private static boolean allowChat(String message) {
        if (!Rp1Command.isRp1(message)) {
            return true;                               // not ours: leave it to other mods / the server
        }
        try {
            handle(Rp1Command.parse(message));
        } catch (Exception e) {
            LOG.warn("rp1: command failed", e);
        }
        return false;                                  // swallow: never reaches the server
    }

    private static void handle(Rp1Command c) {
        Minecraft mc = Minecraft.getInstance();
        switch (c.kind) {
            case HELP -> say(c.message);
            case ERROR -> say(c.message);
            case SIDECAR -> {
                String what = c.cmd;
                client.sendCommand(c.json, r -> REPLIES.add(line(what + ": " + r)));
            }
            case HERE -> {
                if (mc.player == null || mc.level == null) {
                    return;
                }
                BlockPos p = mc.player.blockPosition();
                config.setAnchorBlock(p.getX(), p.getY(), p.getZ(), dimId(mc.level));
                saveConfig();
                say("anchor set to " + p.getX() + " " + p.getY() + " " + p.getZ() + " (" + config.dim + ")");
            }
            case SCALE -> {
                config.scale = c.scale;
                saveConfig();
                say("scale " + c.scale + " (RP1 ~" + Math.round(c.scale * HEIGHT_M * 10) / 10.0 + " blocks tall)");
            }
            default -> {
            }
        }
    }

    static void say(String s) {
        REPLIES.add(line(s));
    }

    static MutableComponent line(String s) {
        return Component.literal("[RP1] ").withColor(0x4FB6FF).append(Component.literal(s).withColor(0xE0E0E0));
    }

    static void saveConfig() {
        try {
            config.save(configFile);
        } catch (IOException e) {
            LOG.warn("rp1: cannot save {}: {}", configFile, e.toString());
        }
    }

    static String dimId(ClientLevel level) {
        return level.dimension().identifier().toString();
    }

    // ---------------------------------------------------------------- tick: chat replies, player position

    private static void tick(Minecraft mc) {
        Component r;
        int n = 0;
        while (n++ < 10 && (r = REPLIES.poll()) != null) {
            if (mc.level != null) {
                mc.gui.hud.getChat().addClientSystemMessage(r);
            }
        }
        boolean here = mc.level != null && mc.player != null && config.enabled && config.hasAnchor()
                && dimId(mc.level).equals(config.dim);
        if (!here) {
            client.setPlayer(null);
        } else if (mc.level.getGameTime() % 4 == 0) {
            client.setPlayer(Rp1Math.mcToSim(config.ax, config.az, config.scale, mc.player.getX(), mc.player.getZ()));
        }
        // voice mic focus (Rp1Focus): claim when in hear range and looked at / nearest; the best claim among the
        // companions (GLaDOS, RP1; the duck yields to any focus owner) gets the push-to-talk
        double score = 0;
        if (here && voiceActive && voiceConfig.enabled) {
            try {
                double[] h = headPos();
                Vec3 e = mc.player.getEyePosition();
                Vec3 v = mc.player.getViewVector(1f);
                double[] eye = {e.x, e.y, e.z};
                double[] view = {v.x, v.y, v.z};
                double dist = Math.sqrt(sq(h[0] - e.x) + sq(h[1] - e.y) + sq(h[2] - e.z));
                double ang = Rp1Focus.angleTo(view, eye, h);
                double[] duck = Rp1Focus.duckPos(config.dim);
                double dd = duck == null ? Double.NaN : Math.sqrt(sq(duck[0] - e.x) + sq(duck[1] - e.y) + sq(duck[2] - e.z));
                double da = duck == null ? Double.NaN : Rp1Focus.angleTo(view, eye, duck);
                score = Rp1Focus.score(dist, voiceConfig.hearRange, ang, voiceConfig.lookAngle, dd, da);
            } catch (RuntimeException ex) {
                score = 0;
            }
        }
        if (score > 0 || focusOwned) {
            focusOwned = Rp1Focus.arbitrate(Rp1Focus.OWNER, score, System.currentTimeMillis());
        } else {
            System.clearProperty(Rp1Focus.CLAIM + Rp1Focus.OWNER);
        }
    }

    private static double sq(double v) {
        return v * v;
    }

    /**
     * His head (voice position + focus target) in MC coords: {@code head_up} sim metres above the origin of the
     * {@code head_body} body (rp1-voice.properties, default waist_yaw_link: the torso + head shell) at the latest pose.
     * Body missing from /model.json: the root (base_link) + 0.5 m. No pose yet: 1.2 blocks above the anchor.
     */
    static double[] headPos() {
        Rp1Config c = config;
        if (c == null) {
            return null;
        }
        SidecarClient sc = client;
        SidecarClient.Frames f = sc == null ? null : sc.frames();
        if (f != null) {
            Integer i = f.model.index.get(voiceConfig.headBody);
            float[] p = i != null && i < f.cur.bodies.length ? f.cur.bodies[i] : null;
            if (p != null) {
                return Rp1Math.bodyVertexToMc(c.ax, c.ay, c.az, c.scale, p, 0, 0, voiceConfig.headUp);
            }
            float[] r = SidecarClient.root(f.cur, f.model);
            if (r != null) {
                return Rp1Math.simToMc(c.ax, c.ay, c.az, c.scale, r[0], r[1], r[2] + 0.5);
            }
        }
        return new double[] {c.ax, c.ay + 1.2 * c.scale, c.az};
    }

    // ---------------------------------------------------------------- rendering

    static boolean renderBroken;

    private static void render(LevelRenderContext ctx) {
        if (renderBroken) {
            return;
        }
        try {
            renderRobot(ctx);
        } catch (RuntimeException e) {
            renderBroken = true;                       // one log line, never spam or crash the frame loop
            LOG.warn("rp1: render failed, RP1 rendering disabled until restart", e);
        }
    }

    private static void renderRobot(LevelRenderContext ctx) {
        Minecraft mc = Minecraft.getInstance();
        if (!config.enabled || !config.hasAnchor() || mc.level == null || !dimId(mc.level).equals(config.dim)) {
            return;
        }
        SidecarClient.Frames f = client.frames();
        if (f == null) {
            return;
        }
        Rp1Model model = f.model;
        int nb = model.bodies.length;
        if (nb == 0) {
            return;
        }
        CameraRenderState cam = ctx.levelState().cameraRenderState;
        Vec3 cp = cam.pos;
        double s = config.scale, ax = config.ax, ay = config.ay, az = config.az;
        float alpha = SidecarClient.alpha(f, System.nanoTime());

        // distance cull on the root body (base_link) before doing any per-body work
        float[] rootPose = interp(f, model.root(), alpha);
        if (rootPose == null) {
            rootPose = SidecarClient.root(f.cur, model);
            if (rootPose == null) {
                return;
            }
        }
        double[] ro = Rp1Math.simToMc(ax, ay, az, s, rootPose[0], rootPose[1], rootPose[2]);
        if (cp.distanceToSqr(ro[0], ro[1], ro[2]) > MAX_DIST * MAX_DIST) {
            return;
        }

        float[][] poses = new float[nb][];
        double minX = Double.MAX_VALUE, minY = Double.MAX_VALUE, minZ = Double.MAX_VALUE;
        double maxX = -Double.MAX_VALUE, maxY = -Double.MAX_VALUE, maxZ = -Double.MAX_VALUE;
        for (int i = 0; i < nb; i++) {
            float[] p = i == model.root() ? rootPose : interp(f, i, alpha);
            if (p == null) {
                continue;
            }
            poses[i] = p;
            double x = ax + s * p[0], y = ay + s * p[2], z = az - s * p[1];
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            minZ = Math.min(minZ, z);
            maxX = Math.max(maxX, x);
            maxY = Math.max(maxY, y);
            maxZ = Math.max(maxZ, z);
        }
        if (minX == Double.MAX_VALUE) {
            return;
        }
        double pad = model.radius * s + 0.25;
        AABB box = new AABB(minX - pad, minY - pad, minZ - pad, maxX + pad, maxY + pad, maxZ + pad);
        Frustum fr = cam.cullFrustum;
        if (fr != null && !fr.isVisible(box)) {
            return;
        }
        // light: at the root's x/z, the air block just above the lowest body (feet) and the root's own block
        BlockPos feet = BlockPos.containing(ro[0], minY + 0.1, ro[2]);
        BlockPos pelvis = BlockPos.containing(ro[0], ro[1], ro[2]);
        int light = LightCoordsUtil.max(LightCoordsUtil.getLightCoords(mc.level, feet),
                LightCoordsUtil.getLightCoords(mc.level, pelvis));

        if (renderType == null) {
            renderType = RenderTypes.entityCutout(WHITE);
        }
        PoseStack ps = ctx.poseStack();
        float fs = (float) s;
        for (int i = 0; i < nb; i++) {
            float[] p = poses[i];
            Rp1Model.Body body = model.bodies[i];
            if (p == null || body.triangles() == 0) {
                continue;
            }
            double[] q = Rp1Math.quatSimToMc(p[3], p[4], p[5], p[6]);
            ps.pushPose();
            ps.translate(ax + s * p[0] - cp.x, ay + s * p[2] - cp.y, az - s * p[1] - cp.z);
            ps.mulPose(new Quaternionf((float) q[1], (float) q[2], (float) q[3], (float) q[0]));
            ps.scale(fs, fs, fs);
            final int lightF = light;
            ctx.submitNodeCollector().submitCustomGeometry(ps, renderType, (pose, vc) -> emit(body, pose, vc, lightF));
            ps.popPose();
        }
    }

    /** Interpolated sim pose of body i, or null if the newest pose lacks it. */
    static float[] interp(SidecarClient.Frames f, int i, float alpha) {
        float[] a = f.prev.bodies[i], b = f.cur.bodies[i];
        if (b == null) {
            return null;
        }
        return a == null ? b : Rp1Math.lerpPose(a, b, alpha, null);
    }

    /** Feeds the pre-baked flat arrays straight into the vertex buffer (one bulk addVertex per vertex). */
    static void emit(Rp1Model.Body body, PoseStack.Pose pose, VertexConsumer vc, int light) {
        pose.pose().get(MAT);
        pose.normal().get(NRM);
        final int overlay = OverlayTexture.NO_OVERLAY;
        Rp1Model.emit(body, MAT, NRM,
                (x, y, z, argb, nx, ny, nz) -> vc.addVertex(x, y, z, argb, 0.5f, 0.5f, overlay, light, nx, ny, nz));
    }
}
