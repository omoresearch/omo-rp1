package dev.yifan.rp1;

import java.lang.reflect.Method;

/**
 * Microphone focus between the lab companions' Simple Voice Chat plugins (GLaDOS, the Microduck(s), the RP1). They
 * all live in one JVM and share System properties, so exactly one of them answers each push-to-talk:
 * <ul>
 *   <li>{@code omo.voice.claim.<owner>} = {@code "<epoch ms>:<score>"}: every client tick, each companion that wants the
 *       mic publishes a claim (no claim = property cleared). Score: looking at it (within look_angle of its head, and
 *       not looking more directly at a duck) = {@code 2 + (1 - angle/180)}, so the most directly looked-at one wins;
 *       otherwise nearer to it than to the duck (and not looking at a duck) = {@code 1 / (1 + dist)}, so the nearest
 *       one wins. Outside its hear_range: no claim.</li>
 *   <li>The highest fresh (&lt; 1 s) claim wins (ties: owner name order). The winner publishes
 *       {@code omo.voice.focus} = {@code "<owner>:<epoch ms>"}; its SVC plugin forwards the mic only while it holds a
 *       fresh focus. The microduck plugin yields to any fresh focus owner that is not a duck.</li>
 * </ul>
 * Pure Java (the duck lookup is reflection), so it is unit-testable without the game.
 */
public final class Rp1Focus {
    public static final String PROP = "omo.voice.focus";
    public static final String CLAIM = "omo.voice.claim.";
    public static final String OWNER = "rp1";
    public static final long FRESH_MS = 1000;

    private Rp1Focus() {
    }

    /**
     * Claim score for one companion (0 = no claim). dist: blocks from the player's eye to its head (NaN = other
     * dimension / unknown); hearRange &lt;= 0 means "always"; angleDeg: between the view direction and the direction to
     * its head; distDuck / angleDuck: the nearest duck's head (NaN = no duck in this world).
     */
    public static double score(double dist, double hearRange, double angleDeg, double maxAngleDeg, double distDuck, double angleDuck) {
        if (Double.isNaN(dist)) {
            return hearRange <= 0 && Double.isNaN(distDuck) ? 0.01 : 0;
        }
        if (hearRange > 0 && dist > hearRange) {
            return 0;
        }
        boolean lookingAtDuck = angleDuck <= maxAngleDeg;                // NaN (no duck) -> false
        if (angleDeg <= maxAngleDeg && !(angleDuck < angleDeg)) {
            return 2 + (1 - angleDeg / 180.0);
        }
        boolean closer = !(distDuck <= dist);                              // NaN (no duck) counts as closer
        if (closer && !lookingAtDuck) {
            return 1 / (1 + dist);
        }
        return 0;
    }

    /** Publish (score &gt; 0) or withdraw this owner's claim for this tick. */
    public static void claim(String owner, double score, long nowMs) {
        if (score > 0) {
            System.setProperty(CLAIM + owner, nowMs + ":" + score);
        } else {
            System.clearProperty(CLAIM + owner);
        }
    }

    /** The owner of the best fresh claim, or null. */
    public static String winner(long nowMs) {
        String best = null;
        double bestScore = 0;
        for (String k : System.getProperties().stringPropertyNames()) {
            if (!k.startsWith(CLAIM)) {
                continue;
            }
            String owner = k.substring(CLAIM.length());
            String v = System.getProperty(k);
            int c = v == null ? -1 : v.indexOf(':');
            if (c <= 0 || owner.isEmpty()) {
                continue;
            }
            try {
                long t = Long.parseLong(v.substring(0, c).trim());
                double sc = Double.parseDouble(v.substring(c + 1).trim());
                long age = nowMs - t;
                if (age < -FRESH_MS || age >= FRESH_MS || !(sc > 0)) {
                    continue;
                }
                if (best == null || sc > bestScore || (sc == bestScore && owner.compareTo(best) < 0)) {
                    best = owner;
                    bestScore = sc;
                }
            } catch (NumberFormatException e) {
                // malformed claim: ignore
            }
        }
        return best;
    }

    /** One client tick for {@code owner}: claim with {@code score}, then hold the focus iff our claim is the best. */
    public static boolean arbitrate(String owner, double score, long nowMs) {
        claim(owner, score, nowMs);
        boolean win = score > 0 && owner.equals(winner(nowMs));
        if (win) {
            System.setProperty(PROP, owner + ":" + nowMs);
        } else {
            release(owner);
        }
        return win;
    }

    /** Drop our focus (only if the property is ours), so the others get the mic back immediately. */
    public static void release(String owner) {
        String v = System.getProperty(PROP);
        if (v != null && v.startsWith(owner + ":")) {
            System.clearProperty(PROP);
        }
    }

    /** Withdraw both the claim and the focus (voice chat stopped / left the world). */
    public static void withdraw(String owner) {
        System.clearProperty(CLAIM + owner);
        release(owner);
    }

    /** True if {@code value} is "owner:ms" with ms no older than FRESH_MS at nowMs. */
    public static boolean heldBy(String owner, String value, long nowMs) {
        if (value == null || !value.startsWith(owner + ":")) {
            return false;
        }
        try {
            long t = Long.parseLong(value.substring(owner.length() + 1).trim());
            long age = nowMs - t;
            return age >= -FRESH_MS && age < FRESH_MS;   // small negative = clock skew between threads: still fresh
        } catch (NumberFormatException e) {
            return false;
        }
    }

    /** Does the RP1 currently own the mic (fresh focus property)? */
    public static boolean ownedNow() {
        return heldBy(OWNER, System.getProperty(PROP), System.currentTimeMillis());
    }

    // ---------------------------------------------------------------- the duck's head (microduck mod, if present)

    private static volatile Method duckPos, duckDim;
    private static volatile boolean duckLookedUp;

    /** Microduck (default duck) head position in MC coords via DuckVoicePlugin.duckPos(), or null (mod absent / no anchor). */
    public static double[] duckPos(String wantDim) {
        try {
            if (!duckLookedUp) {
                duckLookedUp = true;
                Class<?> c = Class.forName("dev.yifan.microduck.DuckVoicePlugin", false, Rp1Focus.class.getClassLoader());
                Method p = c.getDeclaredMethod("duckPos");
                Method d = c.getDeclaredMethod("duckDim");
                p.setAccessible(true);
                d.setAccessible(true);
                duckPos = p;
                duckDim = d;
            }
            Method p = duckPos, d = duckDim;
            if (p == null) {
                return null;
            }
            Object dim = d.invoke(null);
            if (wantDim != null && !wantDim.equals(dim)) {
                return null;
            }
            Object r = p.invoke(null);
            return r instanceof double[] a && a.length >= 3 ? a : null;
        } catch (Throwable t) {                          // class absent (no microduck / no SVC) or API changed
            duckPos = null;
            return null;
        }
    }

    /** Angle in degrees between view vector v (from eye e) and the direction from e to target t. */
    public static double angleTo(double[] v, double[] e, double[] t) {
        double dx = t[0] - e[0], dy = t[1] - e[1], dz = t[2] - e[2];
        double ld = Math.sqrt(dx * dx + dy * dy + dz * dz), lv = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
        if (ld < 1e-9 || lv < 1e-9) {
            return ld < 1e-9 ? 0 : 180;
        }
        double c = (dx * v[0] + dy * v[1] + dz * v[2]) / (ld * lv);
        return Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, c))));
    }
}
