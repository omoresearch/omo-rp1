package dev.yifan.rp1;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Standalone test harness for the mod's pure-Java parts (not loaded by Fabric; not in the jar).
 * Run: ./build.sh test [http://127.0.0.1:PORT]
 */
public final class Harness {
    static int pass, fail;

    static void check(String name, boolean ok, String detail) {
        if (ok) {
            pass++;
        } else {
            fail++;
        }
        System.out.println((ok ? "PASS " : "FAIL ") + name + (detail == null ? "" : "  " + detail));
    }

    static boolean near(double[] a, double[] b, double eps) {
        for (int i = 0; i < a.length; i++) {
            if (Math.abs(a[i] - b[i]) > eps) {
                return false;
            }
        }
        return true;
    }

    static String v(double... d) {
        StringBuilder sb = new StringBuilder("(");
        for (int i = 0; i < d.length; i++) {
            sb.append(i > 0 ? ", " : "").append(String.format(Locale.ROOT, "%.4f", d[i]));
        }
        return sb.append(")").toString();
    }

    public static void main(String[] args) throws Exception {
        String url = args.length > 0 ? args[0] : "http://127.0.0.1:8797";
        math();
        commands();
        config();
        model();
        emitter();
        perf();
        http(url);
        System.out.println();
        System.out.println("RESULT: " + pass + " passed, " + fail + " failed");
        System.exit(fail == 0 ? 0 : 1);
    }

    // ------------------------------------------------------------------ frame mapping

    static void math() {
        System.out.println("== frame mapping (A=(10.5,64,-3.5)) ==");
        double ax = 10.5, ay = 64, az = -3.5, s = 1;
        double[] o = Rp1Math.simToMc(ax, ay, az, s, 0, 0, 0);
        check("sim origin -> anchor", near(o, new double[] {10.5, 64, -3.5}, 1e-9), v(o));
        double[] p = Rp1Math.simToMc(ax, ay, az, s, 0.4, 0.8, 0.2);
        check("s=1: sim (0.4,0.8,0.2) -> MC (10.9,64.2,-4.3)", near(p, new double[] {10.9, 64.2, -4.3}, 1e-9), v(p));
        double[] up = Rp1Math.simToMc(ax, ay, az, s, 0, 0, 1.5);
        check("s=1: sim +z (up) 1.5 m -> MC +y 1.5 blocks", near(up, new double[] {10.5, 65.5, -3.5}, 1e-9), v(up));
        double[] up2 = Rp1Math.simToMc(ax, ay, az, 2, 0, 0, 1.5);
        check("s=2: 1.5 m -> 3 blocks", near(up2, new double[] {10.5, 67, -3.5}, 1e-9), v(up2));
        double[] sy = Rp1Math.simToMc(ax, ay, az, s, 0, 1, 0);
        check("sim +y -> MC -z (north)", near(sy, new double[] {10.5, 64, -4.5}, 1e-9), v(sy));
        double[] back = Rp1Math.mcToSim(ax, az, s, 10.9, -4.3);
        check("player MC (10.9,_,-4.3) -> sim (0.4,0.8)", near(back, new double[] {0.4, 0.8}, 1e-9), v(back));
        double[] back2 = Rp1Math.mcToSim(ax, az, 2, 10.9, -4.3);
        check("s=2: player MC (10.9,_,-4.3) -> sim (0.2,0.4)", near(back2, new double[] {0.2, 0.4}, 1e-9), v(back2));

        double c = Math.cos(Math.PI / 4), sn = Math.sin(Math.PI / 4);
        double[] qmc = Rp1Math.quatSimToMc(c, 0, 0, sn);
        check("yaw90 quat sim [c,0,0,s] -> MC [c,0,s,0] (about +y)", near(qmc, new double[] {c, 0, sn, 0}, 1e-12), v(qmc));
        double[] fwd = Rp1Math.rotate(qmc, 1, 0, 0);
        check("yaw90: sim forward +x ends up MC -z (sim +y)", near(fwd, new double[] {0, 0, -1}, 1e-12), v(fwd));

        double[] q = norm(new double[] {0.7, 0.2, -0.4, 0.55});
        float[] pq = {0.03f, -0.07f, 0.92f, (float) q[0], (float) q[1], (float) q[2], (float) q[3]};
        double[] lv = {0.02, -0.05, 0.08};
        double[] rv = Rp1Math.rotate(q, lv[0], lv[1], lv[2]);
        double[] want = Rp1Math.simToMc(ax, ay, az, 1.7, pq[0] + rv[0], pq[1] + rv[1], pq[2] + rv[2]);
        double[] got = Rp1Math.bodyVertexToMc(ax, ay, az, 1.7, pq, lv[0], lv[1], lv[2]);
        check("general pose: render path == A + s*C*(p + R v)", near(got, want, 1e-5), v(got) + " vs " + v(want));
        double[][] C = {{1, 0, 0}, {0, 0, 1}, {0, -1, 0}};
        double[][] r = Rp1Math.matrix(q);
        double[][] expect = mul(mul(C, r), transpose(C));
        double[][] rmc = Rp1Math.matrix(Rp1Math.quatSimToMc(q[0], q[1], q[2], q[3]));
        double err = 0;
        for (int i = 0; i < 3; i++) {
            for (int j = 0; j < 3; j++) {
                err = Math.max(err, Math.abs(expect[i][j] - rmc[i][j]));
            }
        }
        check("R_mc == C R_sim C^T (max err " + String.format(Locale.ROOT, "%.1e", err) + ")", err < 1e-12, null);

        float[] a = {0, 0, 0, 1, 0, 0, 0};
        float[] b = {1, 2, 3, (float) c, 0, 0, (float) sn};
        float[] m = Rp1Math.lerpPose(a, b, 0.5f, null);
        double yaw = Math.toDegrees(2 * Math.atan2(m[6], m[3]));
        check("lerpPose t=0.5: pos (0.5,1,1.5), yaw 45", Math.abs(yaw - 45) < 1e-3 && near(
                new double[] {m[0], m[1], m[2]}, new double[] {0.5, 1, 1.5}, 1e-6), "yaw=" + String.format(Locale.ROOT, "%.4f", yaw));
        float[] bneg = {1, 2, 3, (float) -c, 0, 0, (float) -sn};
        float[] m2 = Rp1Math.lerpPose(a, bneg, 0.5f, null);
        double yaw2 = Math.toDegrees(2 * Math.atan2(m2[6] * Math.signum(m2[3]), Math.abs(m2[3])));
        check("lerpPose takes shortest arc (q vs -q)", Math.abs(yaw2 - 45) < 1e-3, "yaw=" + String.format(Locale.ROOT, "%.4f", yaw2));
    }

    static double[] norm(double[] q) {
        double n = Math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
        return new double[] {q[0] / n, q[1] / n, q[2] / n, q[3] / n};
    }

    static double[][] mul(double[][] a, double[][] b) {
        double[][] r = new double[3][3];
        for (int i = 0; i < 3; i++) {
            for (int j = 0; j < 3; j++) {
                for (int k = 0; k < 3; k++) {
                    r[i][j] += a[i][k] * b[k][j];
                }
            }
        }
        return r;
    }

    static double[][] transpose(double[][] a) {
        double[][] r = new double[3][3];
        for (int i = 0; i < 3; i++) {
            for (int j = 0; j < 3; j++) {
                r[i][j] = a[j][i];
            }
        }
        return r;
    }

    // ------------------------------------------------------------------ command parser

    static void commands() {
        System.out.println("== command parser ==");
        String[][] cases = {
            {"hello world", "NOT_RP1", null},
            {"!duck walk", "NOT_RP1", null},
            {"!duck", "NOT_RP1", null},
            {"!rp10 walk", "NOT_RP1", null},
            {"!rp1x", "NOT_RP1", null},
            {"!rp1", "HELP", null},
            {"  !RP1  ", "HELP", null},
            {"!rp1 help", "HELP", null},
            {"!rp1 walk", "SIDECAR", "{\"cmd\":\"walk\",\"from\":\"mc\"}"},
            {"!rp1 STOP", "SIDECAR", "{\"cmd\":\"stop\",\"from\":\"mc\"}"},
            {"!rp1 follow", "SIDECAR", "{\"cmd\":\"follow\",\"from\":\"mc\"}"},
            {"!rp1 wave", "SIDECAR", "{\"cmd\":\"wave\",\"from\":\"mc\"}"},
            {"!rp1 dance", "SIDECAR", "{\"cmd\":\"dance\",\"from\":\"mc\"}"},
            {"!rp1 reset", "SIDECAR", "{\"cmd\":\"reset\",\"from\":\"mc\"}"},
            {"!rp1 left", "SIDECAR", "{\"cmd\":\"turn\",\"dir\":\"left\",\"from\":\"mc\"}"},
            {"!rp1 right", "SIDECAR", "{\"cmd\":\"turn\",\"dir\":\"right\",\"from\":\"mc\"}"},
            {"!rp1 here", "HERE", null},
            {"!rp1 scale 2.5", "SCALE", "2.5"},
            {"!rp1 scale 0.25", "SCALE", "0.25"},
            {"!rp1 scale 10", "SCALE", "10.0"},
            {"!rp1 scale 11", "ERROR", null},
            {"!rp1 scale 0.2", "ERROR", null},
            {"!rp1 scale abc", "ERROR", null},
            {"!rp1 scale", "ERROR", null},
            {"!rp1 scale NaN", "ERROR", null},
            {"!rp1 quack", "ERROR", null},
            {"!rp1 sit", "ERROR", null},
        };
        for (String[] t : cases) {
            Rp1Command c = Rp1Command.parse(t[0]);
            boolean ok = c.kind.name().equals(t[1]);
            String det = c.kind + (c.json != null ? " " + c.json : "") + (c.kind == Rp1Command.Kind.SCALE ? " " + c.scale : "")
                    + (c.message != null && c.kind == Rp1Command.Kind.ERROR ? " (" + c.message + ")" : "");
            if (t[2] != null) {
                ok &= c.kind == Rp1Command.Kind.SCALE ? Double.toString(c.scale).equals(t[2]) : t[2].equals(c.json);
            }
            check(String.format("%-18s", "'" + t[0] + "'"), ok, det);
        }
    }

    // ------------------------------------------------------------------ properties round trip

    static void config() throws Exception {
        System.out.println("== config round trip ==");
        Path dir = Files.createTempDirectory("rp1-test");
        Path f = dir.resolve("config/rp1.properties");
        Rp1Config d = Rp1Config.load(f);
        check("missing file -> defaults (port 8797, scale 1.0, no anchor)",
                d.enabled && d.port == 8797 && d.scale == 1.0 && !d.hasAnchor(), null);
        d.save(f);
        String def = Files.readString(f);
        check("defaults file has no anchor keys", !def.contains("anchor.") && def.contains("port=8797") && def.contains("scale=1.0"),
                def.replace("\n", " | "));
        d.setAnchorBlock(-12, 63, 40, "minecraft:overworld");
        d.scale = 2.5;
        d.port = 8799;
        d.enabled = false;
        d.save(f);
        System.out.println("  file: " + Files.readString(f).replace("\n", " | "));
        Rp1Config r = Rp1Config.load(f);
        check("round trip", !r.enabled && r.port == 8799 && r.scale == 2.5 && r.hasAnchor()
                && r.ax == -11.5 && r.ay == 63 && r.az == 40.5 && "minecraft:overworld".equals(r.dim),
                "anchor=" + v(r.ax, r.ay, r.az) + " dim=" + r.dim + " scale=" + r.scale + " port=" + r.port + " enabled=" + r.enabled);
        Files.writeString(f, "scale=99\nport=oops\nanchor.x=1\n");
        Rp1Config bad = Rp1Config.load(f);
        check("bad values clamp/default, partial anchor ignored", bad.scale == 10.0 && bad.port == 8797 && !bad.hasAnchor(),
                "scale=" + bad.scale + " port=" + bad.port + " anchor=" + bad.hasAnchor());
        Files.writeString(f, "scale=0.01\n");
        check("scale clamps up to 0.25", Rp1Config.load(f).scale == 0.25, null);
        check("no .tmp left behind", !Files.exists(f.resolveSibling("rp1.properties.tmp")), null);
    }

    // ------------------------------------------------------------------ model parse

    static void model() {
        System.out.println("== model parse ==");
        String json = "{\"version\":1,\"bodies\":[{\"name\":\"base_link\",\"meshes\":[{\"rgba\":[1,0.5,0,1],"
                + "\"verts\":[0,0,0, 1,0,0, 0,1,0],\"tris\":[0,1,2]},{\"rgba\":[1,1,1,0],\"verts\":[0,0,0,1,0,0,0,1,0],\"tris\":[0,1,2]},"
                + "{\"rgba\":[0,1,0,1],\"verts\":[0,0,0, 0,0,1, 1,0,0],\"tris\":[0,1,2]}]},"
                + "{\"name\":\"left_foot\",\"meshes\":[{\"rgba\":[0,0,1,1],\"verts\":[0,0,0],\"tris\":[0,0,5]}]},"
                + "{\"name\":\"torso\"}]}";
        Rp1Model m = Rp1Model.parse(json);
        Rp1Model.Body b = m.bodies[0];
        check("bodies/order from JSON, root = first (base_link)", m.bodies.length == 3 && m.index.get("left_foot") == 1
                && m.bodies[m.root()].name.equals("base_link") && m.index.get("torso") == 2, null);
        check("meshes merged per body; invisible (alpha 0) skipped; bad indices dropped; no-mesh body ok",
                b.triangles() == 2 && m.bodies[1].triangles() == 0 && m.bodies[2].triangles() == 0 && m.triangles == 2,
                "tris=" + m.triangles);
        check("per-triangle colour (opaque ARGB)", b.col[0] == 0xFFFF8000 && b.col[1] == 0xFF00FF00,
                String.format("0x%08X 0x%08X", b.col[0], b.col[1]));
        check("verts in MC axes", b.pos[6] == 0 && b.pos[7] == 0 && b.pos[8] == -1, null);
        check("flat normal sim +z -> MC +y", near(new double[] {b.nrm[0], b.nrm[1], b.nrm[2]}, new double[] {0, 1, 0}, 1e-6),
                v(b.nrm[0], b.nrm[1], b.nrm[2]));
        check("radius = max local vertex distance", Math.abs(m.radius - 1) < 1e-6, "r=" + m.radius);
    }

    // ------------------------------------------------------------------ baked emitter == contract mapping

    static void emitter() {
        System.out.println("== baked emitter ==");
        String json = "{\"bodies\":[{\"name\":\"base_link\",\"meshes\":[{\"rgba\":[0.2,0.4,0.6,1],"
                + "\"verts\":[0.1,0,0, 0,0.2,0, 0,0,0.3],\"tris\":[0,1,2]}]}]}";
        Rp1Model m = Rp1Model.parse(json);
        double ax = 10.5, ay = 64, az = -3.5, s = 1.3;
        double[] q = norm(new double[] {0.6, -0.3, 0.5, 0.2});
        float[] pose = {0.2f, -0.4f, 0.9f, (float) q[0], (float) q[1], (float) q[2], (float) q[3]};
        double[] o = Rp1Math.simToMc(ax, ay, az, s, pose[0], pose[1], pose[2]);
        double[] qmc = Rp1Math.quatSimToMc(q[0], q[1], q[2], q[3]);
        float[] mat = Rp1Math.bodyMatrix(o[0], o[1], o[2], qmc, s);
        float[] nm = Rp1Math.normalMatrix(qmc);
        List<float[]> out = new ArrayList<>();
        Rp1Model.emit(m.bodies[0], mat, nm, (x, y, z, c, nx, ny, nz) -> out.add(new float[] {x, y, z, c, nx, ny, nz}));
        check("one triangle -> 4 vertices (degenerate quad)", out.size() == 4, "n=" + out.size());
        double[][] sv = {{0.1, 0, 0}, {0, 0.2, 0}, {0, 0, 0.3}};
        double maxErr = 0;
        for (int i = 0; i < 4; i++) {
            double[] w = Rp1Math.bodyVertexToMc(ax, ay, az, s, pose, sv[Math.min(i, 2)][0], sv[Math.min(i, 2)][1], sv[Math.min(i, 2)][2]);
            float[] g = out.get(i);
            for (int k = 0; k < 3; k++) {
                maxErr = Math.max(maxErr, Math.abs(g[k] - w[k]));
            }
        }
        check("emitted vertices == A + s*C*(p + R v) (max err " + String.format(Locale.ROOT, "%.1e", maxErr) + ")",
                maxErr < 1e-4, null);
        // expected world normal: C * R_sim * normalize((v1-v0) x (v2-v0)) in sim coords
        double[] e1 = {-0.1, 0.2, 0}, e2 = {-0.1, 0, 0.3};
        double[] cr = {e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]};
        double ln = Math.sqrt(cr[0] * cr[0] + cr[1] * cr[1] + cr[2] * cr[2]);
        double[] wn = Rp1Math.rotate(q, cr[0] / ln, cr[1] / ln, cr[2] / ln);
        double[] wantN = Rp1Math.axis(wn[0], wn[1], wn[2]);
        float[] g = out.get(0);
        check("emitted normal == C R n", near(new double[] {g[4], g[5], g[6]}, wantN, 1e-5), v(g[4], g[5], g[6]) + " vs " + v(wantN));
    }

    // ------------------------------------------------------------------ cost of a 20k-triangle model

    static double sink;

    static void perf() {
        System.out.println("== perf: 25 bodies x 800 tris = 20k triangles ==");
        StringBuilder sb = new StringBuilder("{\"version\":1,\"bodies\":[");
        java.util.Random rnd = new java.util.Random(1);
        for (int b = 0; b < 25; b++) {
            sb.append(b > 0 ? "," : "").append("{\"name\":\"").append(b == 0 ? "base_link" : "link" + b)
                    .append("\",\"meshes\":[{\"rgba\":[0.8,0.8,0.8,1],\"verts\":[");
            int nv = 450;
            for (int i = 0; i < nv * 3; i++) {
                sb.append(i > 0 ? "," : "").append(String.format(Locale.ROOT, "%.4f", (rnd.nextDouble() - 0.5) * 0.3));
            }
            sb.append("],\"tris\":[");
            for (int i = 0; i < 800 * 3; i++) {
                sb.append(i > 0 ? "," : "").append(rnd.nextInt(nv));
            }
            sb.append("]}]}");
        }
        sb.append("]}");
        long t0 = System.nanoTime();
        Rp1Model m = Rp1Model.parse(sb.toString());
        double parseMs = (System.nanoTime() - t0) / 1e6;
        check("parse " + (sb.length() / 1024) + " KiB model.json", m.triangles == 20000,
                String.format(Locale.ROOT, "%d tris in %.0f ms (once per connect, network thread)", m.triangles, parseMs));
        float[] mat = Rp1Math.bodyMatrix(1, 2, 3, norm(new double[] {0.9, 0.1, 0.2, 0.3}), 1.0);
        float[] nm = Rp1Math.normalMatrix(norm(new double[] {0.9, 0.1, 0.2, 0.3}));
        Rp1Model.Sink s = (x, y, z, c, nx, ny, nz) -> sink += x + y + z + nx;
        for (int w = 0; w < 300; w++) {                        // warm up the JIT
            for (Rp1Model.Body b : m.bodies) {
                Rp1Model.emit(b, mat, nm, s);
            }
        }
        int frames = 300;
        t0 = System.nanoTime();
        for (int f = 0; f < frames; f++) {
            for (Rp1Model.Body b : m.bodies) {
                Rp1Model.emit(b, mat, nm, s);
            }
        }
        double perFrameMs = (System.nanoTime() - t0) / 1e6 / frames;
        check("emit 20k tris (80k vertices) per frame < 2 ms", perFrameMs < 2.0,
                String.format(Locale.ROOT, "%.3f ms/frame (transform only; zero allocation)", perFrameMs));
    }

    // ------------------------------------------------------------------ HTTP against the live (or fake) sidecar

    static void http(String url) throws Exception {
        System.out.println("== HTTP client against " + url + " ==");
        int port = URI.create(url).getPort();
        List<String> logs = new ArrayList<>();
        SidecarClient cl = new SidecarClient(() -> port, () -> true, s -> {
            logs.add(s);
            System.out.println("  [log] " + s);
        });
        cl.start();
        long deadline = System.currentTimeMillis() + 20000;
        while (cl.frames() == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(50);
        }
        SidecarClient.Frames f = cl.frames();
        check("model + first pose fetched", f != null, null);
        if (f == null) {
            cl.stop();
            return;
        }
        Rp1Model m = f.model;
        StringBuilder names = new StringBuilder();
        for (Rp1Model.Body b : m.bodies) {
            names.append(b.name).append(' ');
        }
        System.out.println("  model: " + m.bodies.length + " bodies, " + m.triangles + " tris, max local extent "
                + String.format(Locale.ROOT, "%.3f m", m.radius) + "; bodies: " + names.toString().trim());
        check("root (first) body is base_link", m.bodies.length > 0 && m.bodies[0].name.equals("base_link"),
                m.bodies.length > 0 ? m.bodies[0].name : "none");
        check("triangle budget <= ~20k", m.triangles <= 22000, m.triangles + " tris");
        long seq0 = f.cur.seq;
        long t0 = System.nanoTime();
        int changes = 0;
        long last = seq0;
        while (System.nanoTime() - t0 < 2_000_000_000L) {
            SidecarClient.Frames g = cl.frames();
            if (g != null && g.cur.seq != last) {
                changes++;
                last = g.cur.seq;
            }
            Thread.sleep(5);
        }
        SidecarClient.Frames g = cl.frames();
        check("pose stream advances (" + changes + " new poses in 2 s ~ " + changes / 2 + " Hz)", changes >= 20, null);
        int present = 0;
        for (float[] b : g.cur.bodies) {
            present += b != null ? 1 : 0;
        }
        check("pose covers every model body", present == m.bodies.length, present + "/" + m.bodies.length);
        float[] rt = SidecarClient.root(g.cur, m);
        double[] mc = Rp1Math.simToMc(10.5, 64, -3.5, 1, rt[0], rt[1], rt[2]);
        double minZ = Double.MAX_VALUE, maxZ = -Double.MAX_VALUE;
        for (float[] b : g.cur.bodies) {
            if (b != null) {
                minZ = Math.min(minZ, b[2]);
                maxZ = Math.max(maxZ, b[2]);
            }
        }
        System.out.println("  latest: seq " + g.cur.seq + " mode " + g.cur.mode + " fallen " + g.cur.fallen + " wave "
                + g.cur.wave + "; root sim " + v(rt[0], rt[1], rt[2]) + " -> MC " + v(mc)
                + String.format(Locale.ROOT, "; body origins z %.3f..%.3f m", minZ, maxZ));
        float a = SidecarClient.alpha(g, System.nanoTime());
        check("interp alpha in [0,1] and prev != cur", a >= 0 && a <= 1 && g.prev.seq != g.cur.seq, "alpha=" + a);

        // player position is POSTed at ~5 Hz
        cl.setPlayer(new double[] {0.25, -0.5});
        Thread.sleep(300);
        cl.setPlayer(new double[] {0.3, -0.5});
        Thread.sleep(500);
        String lastJson = rawGet(url + "/_last");
        if (lastJson != null) {
            check("POST /player arrived", lastJson.contains("\"player\": {\"x\": 0.3") || lastJson.contains("\"x\":0.3"), lastJson);
        } else {
            String h = rawGet(url + "/health");
            if (h != null && h.contains("\"player\"")) {
                check("POST /player arrived (real sidecar /health echoes player)", !h.contains("\"player\":null")
                        && !h.contains("\"player\": null"), h);
            } else {
                System.out.println("SKIP POST /player echo (sidecar has no /_last and /health has no player field)  " + h);
            }
        }

        // command round trip: wave (harmless: plays once, then back to stand) and the wave counter
        long w0 = cl.frames().cur.wave;
        CompletableFuture<String> rep = new CompletableFuture<>();
        cl.sendCommand(Rp1Command.parse("!rp1 wave").json, rep::complete);
        String r = rep.get(5, TimeUnit.SECONDS);
        check("POST /cmd wave -> reply", r.startsWith("ok"), r);
        long w1 = w0;
        long until = System.currentTimeMillis() + 3000;
        while (System.currentTimeMillis() < until && w1 <= w0) {
            Thread.sleep(50);
            SidecarClient.Frames h = cl.frames();
            if (h != null) {
                w1 = h.cur.wave;
            }
        }
        check("pose.wave counter increments after wave", w1 > w0, w0 + " -> " + w1);
        CompletableFuture<String> rep3 = new CompletableFuture<>();
        cl.sendCommand("{\"cmd\":\"nonsense\",\"from\":\"mc\"}", rep3::complete);
        String r3 = rep3.get(5, TimeUnit.SECONDS);
        check("unknown cmd -> readable error", r3.startsWith("error"), r3);
        cl.stop();

        System.out.println("== sidecar down (port 1) ==");
        List<String> dl = new ArrayList<>();
        SidecarClient dead = new SidecarClient(() -> 1, () -> true, dl::add);
        dead.start();
        Thread.sleep(4000);
        CompletableFuture<String> rep2 = new CompletableFuture<>();
        dead.sendCommand(Rp1Command.parse("!rp1 walk").json, rep2::complete);
        String r2 = rep2.get(5, TimeUnit.SECONDS);
        check("down: frames null", dead.frames() == null, null);
        check("down: exactly one log line in 4 s of retries", dl.size() == 1, dl.toString());
        check("down: command reply is friendly", r2.contains("not reachable"), r2);
        dead.stop();
    }

    static String rawGet(String url) {
        try {
            HttpResponse<String> r = HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create(url)).build(),
                    HttpResponse.BodyHandlers.ofString());
            return r.statusCode() == 200 ? r.body() : null;
        } catch (Exception e) {
            return null;
        }
    }
}
