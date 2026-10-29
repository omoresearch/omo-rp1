package dev.yifan.rp1;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * Geometry from GET /model.json, baked ONCE for rendering: per body, all visual meshes are merged into flat arrays
 * (9 floats of MC-axis body-local vertices, 3 floats of flat unit normal and one ARGB colour per triangle). The
 * per-frame work is then a tight loop over primitive arrays ({@link #emit}): 3 position transforms + 1 normal
 * transform per triangle, no allocation, no per-vertex object or matrix-stack calls. Body names come from the JSON;
 * nothing is hardcoded. Pure Java (Gson only).
 */
public final class Rp1Model {

    /** Receives transformed vertices; the mod adapts this to a Minecraft VertexConsumer. */
    public interface Sink {
        void vertex(float x, float y, float z, int argb, float nx, float ny, float nz);
    }

    public static final class Body {
        public final String name;
        /** 9 floats per triangle: MC-axis (C v) body-local vertices, metres. */
        public final float[] pos;
        /** 3 floats per triangle: flat unit normal, MC-axis body-local. */
        public final float[] nrm;
        /** 1 int per triangle: 0xFFRRGGBB. */
        public final int[] col;

        Body(String name, float[] pos, float[] nrm, int[] col) {
            this.name = name;
            this.pos = pos;
            this.nrm = nrm;
            this.col = col;
        }

        public int triangles() {
            return col.length;
        }
    }

    public final Body[] bodies;
    public final Map<String, Integer> index = new HashMap<>();
    public final int triangles;
    /** Largest distance (metres) of any vertex from its own body origin; pads the culling box. */
    public final float radius;

    private Rp1Model(Body[] bodies, int triangles, float radius) {
        this.bodies = bodies;
        this.triangles = triangles;
        this.radius = radius;
        for (int i = 0; i < bodies.length; i++) {
            index.putIfAbsent(bodies[i].name, i);
        }
    }

    /** The root (floating-base) body: the first body in /model.json (base_link on the real RP1). */
    public int root() {
        return 0;
    }

    public static Rp1Model parse(String json) {
        JsonObject root = JsonParser.parseString(json).getAsJsonObject();
        JsonArray bs = root.getAsJsonArray("bodies");
        List<Body> out = new ArrayList<>();
        int total = 0;
        float maxR = 0;
        for (JsonElement be : bs) {
            JsonObject b = be.getAsJsonObject();
            String name = b.get("name").getAsString();
            JsonArray ms = b.has("meshes") && b.get("meshes").isJsonArray() ? b.getAsJsonArray("meshes") : new JsonArray();
            // first pass: size
            int cap = 0;
            for (JsonElement me : ms) {
                JsonObject m = me.getAsJsonObject();
                cap += m.has("tris") ? m.getAsJsonArray("tris").size() / 3 : 0;
            }
            float[] pos = new float[cap * 9];
            float[] nrm = new float[cap * 3];
            int[] col = new int[cap];
            int kept = 0;
            for (JsonElement me : ms) {
                JsonObject m = me.getAsJsonObject();
                int argb = argb(m.has("rgba") ? m.getAsJsonArray("rgba") : null);
                if ((argb >>> 24) < 26) {
                    continue;                      // ~invisible geoms (alpha < 0.1) would be cut out anyway
                }
                argb |= 0xFF000000;
                float[] v = floats(m.getAsJsonArray("verts"));
                int[] idx = ints(m.getAsJsonArray("tris"));
                int nt = idx.length / 3;
                int before = kept;
                for (int t = 0; t < nt; t++) {
                    int k0 = idx[3 * t], k1 = idx[3 * t + 1], k2 = idx[3 * t + 2];
                    if (!valid(k0, v) || !valid(k1, v) || !valid(k2, v)) {
                        continue;
                    }
                    int o = kept * 9;
                    put(pos, o, v, k0);
                    put(pos, o + 3, v, k1);
                    put(pos, o + 6, v, k2);
                    float e1x = pos[o + 3] - pos[o], e1y = pos[o + 4] - pos[o + 1], e1z = pos[o + 5] - pos[o + 2];
                    float e2x = pos[o + 6] - pos[o], e2y = pos[o + 7] - pos[o + 1], e2z = pos[o + 8] - pos[o + 2];
                    float nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
                    float len = (float) Math.sqrt(nx * nx + ny * ny + nz * nz);
                    if (len < 1e-12f) {
                        nx = 0;
                        ny = 1;
                        nz = 0;
                    } else {
                        nx /= len;
                        ny /= len;
                        nz /= len;
                    }
                    nrm[kept * 3] = nx;
                    nrm[kept * 3 + 1] = ny;
                    nrm[kept * 3 + 2] = nz;
                    col[kept] = argb;
                    kept++;
                }
                if (kept > before) {
                    for (int i = 0; i + 2 < v.length; i += 3) {
                        maxR = Math.max(maxR, (float) Math.sqrt(v[i] * v[i] + v[i + 1] * v[i + 1] + v[i + 2] * v[i + 2]));
                    }
                }
            }
            if (kept != cap) {
                pos = Arrays.copyOf(pos, kept * 9);
                nrm = Arrays.copyOf(nrm, kept * 3);
                col = Arrays.copyOf(col, kept);
            }
            total += kept;
            out.add(new Body(name, pos, nrm, col));
        }
        return new Rp1Model(out.toArray(new Body[0]), total, maxR);
    }

    private static boolean valid(int k, float[] v) {
        return k >= 0 && 3 * k + 2 < v.length;
    }

    /** Sim vertex k -> MC axes C(x,y,z) = (x, z, -y). */
    private static void put(float[] dst, int o, float[] v, int k) {
        dst[o] = v[3 * k];
        dst[o + 1] = v[3 * k + 2];
        dst[o + 2] = -v[3 * k + 1];
    }

    /**
     * Emit a body as degenerate quads (entity render types draw QUADS; the 3rd vertex is repeated). {@code m} is the
     * column-major 4x4 pose matrix and {@code n} the column-major 3x3 normal matrix (JOML {@code get(float[])} layout).
     */
    public static void emit(Body b, float[] m, float[] n, Sink sink) {
        final float m00 = m[0], m01 = m[1], m02 = m[2];
        final float m10 = m[4], m11 = m[5], m12 = m[6];
        final float m20 = m[8], m21 = m[9], m22 = m[10];
        final float m30 = m[12], m31 = m[13], m32 = m[14];
        final float n00 = n[0], n01 = n[1], n02 = n[2];
        final float n10 = n[3], n11 = n[4], n12 = n[5];
        final float n20 = n[6], n21 = n[7], n22 = n[8];
        final float[] p = b.pos, nr = b.nrm;
        final int[] col = b.col;
        final int nt = col.length;
        for (int t = 0; t < nt; t++) {
            int k = 3 * t;
            float lx = nr[k], ly = nr[k + 1], lz = nr[k + 2];
            float nx = n00 * lx + n10 * ly + n20 * lz;
            float ny = n01 * lx + n11 * ly + n21 * lz;
            float nz = n02 * lx + n12 * ly + n22 * lz;
            float inv = nx * nx + ny * ny + nz * nz;
            if (inv > 0) {
                inv = (float) (1.0 / Math.sqrt(inv));
                nx *= inv;
                ny *= inv;
                nz *= inv;
            }
            int c = col[t];
            int o = 9 * t;
            float x = p[o], y = p[o + 1], z = p[o + 2];
            sink.vertex(m00 * x + m10 * y + m20 * z + m30, m01 * x + m11 * y + m21 * z + m31,
                    m02 * x + m12 * y + m22 * z + m32, c, nx, ny, nz);
            x = p[o + 3];
            y = p[o + 4];
            z = p[o + 5];
            sink.vertex(m00 * x + m10 * y + m20 * z + m30, m01 * x + m11 * y + m21 * z + m31,
                    m02 * x + m12 * y + m22 * z + m32, c, nx, ny, nz);
            x = p[o + 6];
            y = p[o + 7];
            z = p[o + 8];
            float tx = m00 * x + m10 * y + m20 * z + m30, ty = m01 * x + m11 * y + m21 * z + m31,
                    tz = m02 * x + m12 * y + m22 * z + m32;
            sink.vertex(tx, ty, tz, c, nx, ny, nz);
            sink.vertex(tx, ty, tz, c, nx, ny, nz);
        }
    }

    static int argb(JsonArray rgba) {
        float r = 0.8f, g = 0.8f, b = 0.8f, a = 1f;
        if (rgba != null && rgba.size() >= 3) {
            r = rgba.get(0).getAsFloat();
            g = rgba.get(1).getAsFloat();
            b = rgba.get(2).getAsFloat();
            a = rgba.size() >= 4 ? rgba.get(3).getAsFloat() : 1f;
        }
        return (c(a) << 24) | (c(r) << 16) | (c(g) << 8) | c(b);
    }

    private static int c(float f) {
        return Math.max(0, Math.min(255, Math.round(f * 255f)));
    }

    private static float[] floats(JsonArray a) {
        float[] f = new float[a == null ? 0 : a.size()];
        for (int i = 0; i < f.length; i++) {
            f[i] = a.get(i).getAsFloat();
        }
        return f;
    }

    private static int[] ints(JsonArray a) {
        int[] f = new int[a == null ? 0 : a.size()];
        for (int i = 0; i < f.length; i++) {
            f[i] = a.get(i).getAsInt();
        }
        return f;
    }
}
