package dev.yifan.rp1;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.IntSupplier;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * Talks to the rp1-sim sidecar on 127.0.0.1 from one daemon thread: fetches /model.json (retrying with
 * backoff), polls /pose at ~25 Hz, POSTs /player at ~5 Hz. The render/tick threads only read the volatile
 * {@link #frames()} snapshot and never block on HTTP. Pure Java (java.net.http + Gson).
 */
public final class SidecarClient implements Runnable {

    /** One received /pose, with body transforms aligned to the model's body order. */
    public static final class Pose {
        public final long seq;
        public final double t;
        public final String mode;
        public final boolean fallen;
        public final long wave;
        /** [bodyIndex] -> {px,py,pz,qw,qx,qy,qz} in the sim frame, or null if absent. */
        public final float[][] bodies;
        public final long recvNanos;

        Pose(long seq, double t, String mode, boolean fallen, long wave, float[][] bodies, long recvNanos) {
            this.seq = seq;
            this.t = t;
            this.mode = mode;
            this.fallen = fallen;
            this.wave = wave;
            this.bodies = bodies;
            this.recvNanos = recvNanos;
        }
    }

    /** Last two poses (prev may equal cur), for interpolation. */
    public static final class Frames {
        public final Rp1Model model;
        public final Pose prev;
        public final Pose cur;

        Frames(Rp1Model model, Pose prev, Pose cur) {
            this.model = model;
            this.prev = prev;
            this.cur = cur;
        }
    }

    public static final long POSE_PERIOD_MS = 40;      // 25 Hz
    public static final long PLAYER_PERIOD_MS = 200;   // 5 Hz

    private final IntSupplier port;
    private final BooleanSupplier enabled;
    private final Consumer<String> log;
    private final HttpClient http;
    private volatile boolean running = true;
    private volatile Rp1Model model;
    private volatile Frames frames;
    private volatile double[] playerSim;             // latest {x,y} in sim metres, or null
    private boolean down;
    private boolean everUp;

    public SidecarClient(IntSupplier port, BooleanSupplier enabled, Consumer<String> log) {
        this.port = port;
        this.enabled = enabled;
        this.log = log;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(500))
                .build();
    }

    public Thread start() {
        Thread t = new Thread(this, "rp1-sidecar");
        t.setDaemon(true);
        t.setPriority(Thread.MIN_PRIORITY);
        t.start();
        return t;
    }

    public void stop() {
        running = false;
    }

    /** Null while the sidecar is down or before the first pose. */
    public Frames frames() {
        return frames;
    }

    public Rp1Model model() {
        return model;
    }

    /** Player position in sim metres (null = not in a world / anchor not set). Sent at ~5 Hz. */
    public void setPlayer(double[] simXY) {
        playerSim = simXY;
    }

    public String base() {
        return "http://127.0.0.1:" + port.getAsInt();
    }

    @Override
    public void run() {
        long backoff = 1000;
        long nextPlayer = 0;
        double[] lastSentPlayer = null;
        while (running) {
            try {
                if (!enabled.getAsBoolean()) {
                    frames = null;
                    Thread.sleep(500);
                    continue;
                }
                if (model == null) {
                    model = Rp1Model.parse(get("/model.json", 15000));
                    markUp("model: " + model.bodies.length + " bodies, " + model.triangles + " triangles");
                    backoff = 1000;
                }
                long t0 = System.nanoTime();
                Pose p = parsePose(get("/pose", 1000), model, System.nanoTime());
                Frames f = frames;
                if (f == null || f.model != model) {
                    frames = new Frames(model, p, p);
                } else if (p.seq != f.cur.seq) {
                    frames = new Frames(model, f.cur, p);
                }
                markUp(null);
                backoff = 1000;
                long now = System.currentTimeMillis();
                double[] pl = playerSim;
                if (pl != null && now >= nextPlayer && pl != lastSentPlayer) {
                    nextPlayer = now + PLAYER_PERIOD_MS;
                    lastSentPlayer = pl;
                    post("/player", "{\"x\":" + num(pl[0]) + ",\"y\":" + num(pl[1]) + "}", 1000);
                }
                long sleep = POSE_PERIOD_MS - (System.nanoTime() - t0) / 1_000_000L;
                if (sleep > 0) {
                    Thread.sleep(sleep);
                }
            } catch (InterruptedException ie) {
                return;
            } catch (Exception e) {
                frames = null;                        // nothing renders while the sidecar is down
                if (!down) {
                    down = true;
                    log.accept("sidecar " + base() + " unavailable (" + e.getClass().getSimpleName()
                            + (e.getMessage() != null ? ": " + e.getMessage() : "") + "); retrying quietly");
                    model = null;                     // it may come back with different geometry
                }
                try {
                    Thread.sleep(backoff);
                } catch (InterruptedException ie) {
                    return;
                }
                backoff = Math.min(backoff * 2, 15000);
            }
        }
    }

    private void markUp(String detail) {
        if (down || !everUp) {
            log.accept("sidecar " + base() + " connected" + (detail != null ? " (" + detail + ")" : ""));
            down = false;
            everUp = true;
        } else if (detail != null) {
            log.accept(detail);
        }
    }

    /** Fire-and-forget POST /cmd; reply gets a one-line human result (on an HttpClient thread). */
    public void sendCommand(String json, Consumer<String> reply) {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base() + "/cmd"))
                .timeout(Duration.ofSeconds(2))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();
        http.sendAsync(req, HttpResponse.BodyHandlers.ofString()).whenComplete((r, err) -> {
            if (err != null) {
                reply.accept("sidecar not reachable at " + base());
                return;
            }
            try {
                JsonObject o = JsonParser.parseString(r.body()).getAsJsonObject();
                boolean ok = o.has("ok") && o.get("ok").getAsBoolean();
                if (ok) {
                    reply.accept("ok" + (o.has("mode") ? " (mode: " + str(o.get("mode")) + ")" : ""));
                } else {
                    reply.accept("error: " + (o.has("error") ? str(o.get("error")) : "HTTP " + r.statusCode()));
                }
            } catch (Exception e) {
                reply.accept("bad reply (HTTP " + r.statusCode() + ")");
            }
        });
    }

    private static String str(JsonElement e) {
        return e == null || e.isJsonNull() ? "?" : e.isJsonPrimitive() ? e.getAsString() : e.toString();
    }

    String get(String path, long timeoutMs) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base() + path))
                .timeout(Duration.ofMillis(timeoutMs)).GET().build();
        HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (r.statusCode() != 200) {
            throw new java.io.IOException("HTTP " + r.statusCode() + " for " + path);
        }
        return r.body();
    }

    void post(String path, String json, long timeoutMs) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base() + path))
                .timeout(Duration.ofMillis(timeoutMs))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json)).build();
        http.send(req, HttpResponse.BodyHandlers.discarding());
    }

    static String num(double d) {
        return Double.isFinite(d) ? Double.toString(d) : "0";
    }

    public static Pose parsePose(String json, Rp1Model model, long recvNanos) {
        JsonObject o = JsonParser.parseString(json).getAsJsonObject();
        float[][] bodies = new float[model.bodies.length][];
        JsonObject b = o.has("bodies") ? o.getAsJsonObject("bodies") : new JsonObject();
        for (var e : b.entrySet()) {
            Integer i = model.index.get(e.getKey());
            if (i == null || !e.getValue().isJsonArray() || e.getValue().getAsJsonArray().size() < 7) {
                continue;
            }
            float[] v = new float[7];
            for (int k = 0; k < 7; k++) {
                v[k] = e.getValue().getAsJsonArray().get(k).getAsFloat();
            }
            float n = (float) Math.sqrt(v[3] * v[3] + v[4] * v[4] + v[5] * v[5] + v[6] * v[6]);
            if (!(n > 1e-6f) || !Float.isFinite(v[0] + v[1] + v[2])) {
                continue;
            }
            for (int k = 3; k < 7; k++) {
                v[k] /= n;
            }
            bodies[i] = v;
        }
        return new Pose(
                o.has("seq") ? o.get("seq").getAsLong() : 0,
                o.has("t") ? o.get("t").getAsDouble() : 0,
                o.has("mode") ? str(o.get("mode")) : "?",
                o.has("fallen") && o.get("fallen").getAsBoolean(),
                o.has("wave") ? o.get("wave").getAsLong() : 0,
                bodies, recvNanos);
    }

    /** The root body's sim pose (first body of /model.json, e.g. base_link), else the first body present. */
    public static float[] root(Pose p, Rp1Model m) {
        if (p.bodies.length > 0 && p.bodies[m.root()] != null) {
            return p.bodies[m.root()];
        }
        for (float[] b : p.bodies) {
            if (b != null) {
                return b;
            }
        }
        return null;
    }

    /**
     * Interpolation factor between prev and cur: we display one poll interval behind the newest pose, so
     * alpha = (now - cur.recv) / (cur.recv - prev.recv), clamped to [0, 1].
     */
    public static float alpha(Frames f, long nowNanos) {
        long span = f.cur.recvNanos - f.prev.recvNanos;
        if (span <= 0) {
            return 1f;
        }
        float a = (float) (nowNanos - f.cur.recvNanos) / (float) span;
        return a < 0 ? 0 : a > 1 ? 1 : a;
    }
}
